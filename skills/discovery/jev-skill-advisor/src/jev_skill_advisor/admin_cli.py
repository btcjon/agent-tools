from __future__ import annotations
import argparse, json
from pathlib import Path
from .profile import load_profile
from .runtime import ServiceRuntime

def main(argv=None):
    parser=argparse.ArgumentParser(prog="skill-advisor-admin")
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("command",choices=("init-db","migrate-legacy","prune","status","doctor"))
    args=parser.parse_args(argv); runtime=ServiceRuntime(load_profile(args.config),initialize=args.command in {"init-db","migrate-legacy"},allow_pending_legacy=args.command=="migrate-legacy")
    if args.command=="init-db": result={"status":"ready","database":str(runtime.db_path)}
    elif args.command=="migrate-legacy": result=runtime.migrate_legacy()
    elif args.command=="prune": result={"status":"pruned",**runtime.prune()}
    elif args.command=="status": result={"status":"ready","database":str(runtime.db_path),**runtime.stats()}
    else:
        profile=runtime.profile
        result={"status":"ready","profile_id":profile.profile_id,"harness":profile.harness,"mode":profile.mode,
            "provider_enabled":profile.provider_enabled,"provider_credential_available":bool(runtime.key),
            "read_enabled":profile.read_enabled,"eligible_skills":len(profile.eligible_ids),
            "read_allowlist":len(profile.read_allowlist),"catalog_hash":profile.catalog_hash,
            "database":str(runtime.db_path),"telemetry":runtime.stats()}
    print(json.dumps(result,sort_keys=True,separators=(",",":"))); return 0

if __name__=="__main__": raise SystemExit(main())
