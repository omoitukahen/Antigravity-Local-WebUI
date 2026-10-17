from urllib.parse import urlsplit
from .output import allocate_output, validate_text, OutputError

def tool_decision(payload, output_root=None, output_used=False):
    call = payload.get("toolCall", {})
    name, args = call.get("name"), call.get("args")
    if not isinstance(args, dict):
        return False, "unrecognized_arguments"
    if name == 'write_to_file' and output_root and not output_used:
        try:
            required={'TargetFile','Overwrite','CodeContent','Description'}
            if not required<=set(args) or not set(args)<=required|{'IsArtifact','ArtifactMetadata','toolAction','toolSummary'}:
                return False,'unrecognized_arguments'
            if any(not isinstance(args[k],str) for k in ('toolAction','toolSummary') if k in args):
                return False,'unrecognized_arguments'
            if args['Overwrite'] is not False or args.get('IsArtifact',False) is not False or args.get('ArtifactMetadata') is not None:
                return False,'output_overwrite_or_artifact'
            if not all(isinstance(args[k],str) for k in ('TargetFile','CodeContent','Description')):
                return False,'unrecognized_arguments'
            allocate_output(args['TargetFile'],output_root)
            validate_text(args['CodeContent'].encode('utf-8'))
            return True,'text_output'
        except (OSError,ValueError,OutputError):
            return False,'invalid_output_file'
    # Strict candidate adapters. Real capability remains locked until live acceptance.
    if name == "search_web":
        return isinstance(args.get("query"), str) and bool(args["query"].strip()) and set(args) <= {"query", "domain"} and ('domain' not in args or isinstance(args['domain'],str)), "web_search"
    if name == "read_url_content" and set(args) == {"Url"} and isinstance(args["Url"], str):
        try:
            url = urlsplit(args["Url"])
            return url.scheme in {"http", "https"} and bool(url.hostname) and not url.username and not url.password, "web_read"
        except ValueError:
            return False, "invalid_url"
    return False, "tool_not_allowed"
