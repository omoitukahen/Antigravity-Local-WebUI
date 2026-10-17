"""Owned text artifacts: the same checks guard tool writes and downloads."""
import hashlib
from pathlib import Path
import stat
import os
import re

MAX_BYTES = 1024 * 1024


class OutputError(ValueError):
    pass


def registered_folder(value):
    """A host-local existing folder, with no reparse points in any ancestor."""
    path=Path(value)
    if not re.match(r'^[A-Za-z]:[\\/]',value) or not path.is_absolute() or '..' in path.parts:
        raise OutputError('このPC上の既存フォルダーをドライブ付きの絶対パスで指定してください。')
    try:
        for parent in (path,*path.parents):
            metadata=parent.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OutputError('リンクを含む保存先は使用できません。')
    except OSError as exc:raise OutputError('保存先の既存フォルダーを確認できません。') from exc
    if not path.is_dir():raise OutputError('保存先には既存フォルダーを指定してください。')
    return path


def output_name(name):
    if (not name or Path(name).name!=name or '/' in name or '\\' in name
        or Path(name).suffix.lower()!='.txt' or os.path.isreserved(name)
        or name.endswith((' ','.')) or len(name.encode('utf-16-le'))>400):
        raise OutputError('出力名にはWindowsで使用できる200文字以内の.txtファイル名を指定してください。')
    return name


def allocate_output(value,root):
    root=registered_folder(str(root))
    candidate=Path(value)
    if not candidate.is_absolute() or candidate.parent!=root:
        raise OutputError('登録フォルダー直下の.txtだけ作成できます。')
    output_name(candidate.name)
    stem=candidate.stem
    for number in range(1,10000):
        name=candidate.name if number==1 else stem+' ('+str(number)+').txt'
        output_name(name)
        path=owned_path(root/name,root)
        if not path.exists():return path
    raise OutputError('連番の空きがありません。別のファイル名を指定してください。')


def owned_path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if path == root or not path.is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()):
        raise OutputError('出力先が会話の保存領域外です。')
    for parent in (path, *path.parents):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata and (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise OutputError('リンクを含む出力先は使用できません。')
    return path


def validate_text(raw):
    if len(raw) > MAX_BYTES:
        raise OutputError('出力ファイルの上限は1MiBです。')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise OutputError('出力ファイルがUTF-8ではありません。') from exc
    if not text.strip() or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        raise OutputError('出力ファイルが空か、テキスト形式ではありません。')


def read_output(path, root):
    path = owned_path(path, root)
    with path.open('rb') as file:
        import os
        metadata = os.fstat(file.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > MAX_BYTES:
            raise OutputError('通常の1MiB以下の単独ファイルだけを保存できます。')
        raw = file.read(MAX_BYTES + 1)
        owned_path(path, root)
        if path.stat().st_ino != metadata.st_ino:
            raise OutputError('検証中に出力ファイルが変更されました。')
    validate_text(raw)
    return raw, {'name': path.name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def finish_output(path, root, completed, written):
    result={'name':Path(path).name,'state':'missing','size':0,'sha256':None}
    try:
        if not written:
            return result
        _,metadata=read_output(path,root)
        return {**metadata,'state':'completed' if completed else 'incomplete'}
    except FileNotFoundError:
        return result
    except (OSError,ValueError):
        return {**result,'state':'invalid'}
