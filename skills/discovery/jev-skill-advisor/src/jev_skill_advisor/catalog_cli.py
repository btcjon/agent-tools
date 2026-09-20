from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
import yaml
from .profile import current_policy, ProfileError

class _UniqueSafeLoader(yaml.SafeLoader):
    pass

def _mapping(loader,node,deep=False):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if key in result: raise ValueError(f"duplicate_frontmatter_key:{key}")
        result[key]=loader.construct_object(value_node,deep=deep)
    return result

_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_mapping)

def frontmatter(text):
    lines=text.splitlines()
    if not lines or lines[0].strip()!="---": raise ValueError("missing_frontmatter")
    end=next((index for index,line in enumerate(lines[1:],1) if line.strip()=="---"),None)
    if end is None: raise ValueError("unclosed_frontmatter")
    try: result=yaml.load("\n".join(lines[1:end]),Loader=_UniqueSafeLoader)
    except ValueError: raise
    except yaml.YAMLError as exc: raise ValueError("malformed_frontmatter") from exc
    if not isinstance(result,dict): raise ValueError("malformed_frontmatter")
    return result

def _metadata_text(value,key):
    if not isinstance(value,str) or not value.strip(): raise ValueError(f"missing_{key}")
    cleaned=value.strip()
    if re.fullmatch(r"(?i:true|false|null|~|[-+]?\d+(?:\.\d+)?)",cleaned): raise ValueError(f"non_string_{key}")
    return cleaned

def build_catalog(warehouse):
    if not warehouse.exists() or not warehouse.is_dir(): raise ValueError("warehouse_not_directory")
    warehouse=warehouse.resolve(); entries=[]; exclusions=[]
    try: sources=sorted(warehouse.glob("**/SKILL.md"))
    except OSError as exc: raise ValueError("warehouse_inventory_unreadable") from exc
    for source in sources:
        rel=source.parent.relative_to(warehouse).as_posix(); stable_id=f"warehouse:{rel}"
        try:
            if source.is_symlink() or warehouse not in source.resolve().parents: raise ValueError("symlink_or_path_escape")
            try: raw=source.read_bytes()
            except OSError as exc: raise ValueError("warehouse_inventory_unreadable") from exc
            meta=frontmatter(raw.decode("utf-8",errors="replace")); name=_metadata_text(meta.get("name"),"name"); description=_metadata_text(meta.get("description"),"description")
            implicit,policy_hash=current_policy(source)
            if description.lower().startswith("[hermes control: disabled]") or "jb-disabled route" in description.lower():
                exclusions.append({"stable_id":stable_id,"relative_path":rel,"reason":"explicitly_disabled","name":name,"description":description,"content_hash":hashlib.sha256(raw).hexdigest(),"policy_hash":policy_hash}); continue
            protected=any(marker in description.lower() for marker in ("typesafe_api_key=","jev_api=","authorization: bearer","-----begin "))
            entries.append({"stable_id":stable_id,"name":name,"description":description,"relative_path":rel,
                "content_hash":hashlib.sha256(raw).hexdigest(),"policy_hash":policy_hash,"implicit_eligible":implicit,
                "provider_disclosure_eligible":implicit and not protected,"provider_exclusion_reason":"protected_description" if protected else ("explicit_only" if not implicit else None),
                "compatibility":{"agent_skills_structure":True,"declared_harnesses":[],"undeclared_harness_compatibility":"unknown"},"review_status":"canonical_source_metadata"})
        except ValueError as exc:
            if str(exc)=="warehouse_inventory_unreadable": raise
            exclusions.append({"stable_id":stable_id,"relative_path":rel,"reason":str(exc)[:200]})
        except (ProfileError,OSError) as exc:
            exclusions.append({"stable_id":stable_id,"relative_path":rel,"reason":str(exc)[:200]})
    ids=[row["stable_id"] for row in entries]
    if len(ids)!=len(set(ids)): raise ValueError("duplicate_stable_id")
    digest=hashlib.sha256(json.dumps([(row["stable_id"],row["content_hash"],row["policy_hash"]) for row in entries],separators=(",",":"),sort_keys=True).encode()).hexdigest()
    return {"schema_version":1,"catalog_hash":digest,"warehouse_root":str(warehouse),"skill_files":len(entries)+len(exclusions),
        "included_count":len(entries),"excluded_count":len(exclusions),"entries":entries,"exclusions":exclusions}

def main(argv=None):
    parser=argparse.ArgumentParser(prog="skill-advisor-catalog"); parser.add_argument("--warehouse",type=Path,required=True); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args(argv)
    result=build_catalog(args.warehouse); args.output.parent.mkdir(parents=True,exist_ok=True); temporary=args.output.with_suffix(args.output.suffix+".tmp"); temporary.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8"); temporary.replace(args.output)
    print(json.dumps({key:result[key] for key in ("catalog_hash","skill_files","included_count","excluded_count")},sort_keys=True)); return 0 if not result["exclusions"] else 3

if __name__=="__main__": raise SystemExit(main())
