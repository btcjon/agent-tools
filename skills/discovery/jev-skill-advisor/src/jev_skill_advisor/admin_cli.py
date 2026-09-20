from __future__ import annotations
import argparse, json
from pathlib import Path
from .profile import load_profile
from .runtime import ServiceRuntime

def main(argv=None):
    parser=argparse.ArgumentParser(prog="skill-advisor-admin")
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("command",choices=("init-db","migrate-legacy","prune","status"))
    args=parser.parse_args(argv); runtime=ServiceRuntime(load_profile(args.config),initialize=args.command in {"init-db","migrate-legacy"},allow_pending_legacy=args.command=="migrate-legacy")
    if args.command=="init-db": result={"status":"ready","database":str(runtime.db_path)}
    elif args.command=="migrate-legacy": result=runtime.migrate_legacy()
    elif args.command=="prune": result={"status":"pruned",**runtime.prune()}
    else: result={"status":"ready","database":str(runtime.db_path),"counts":runtime.counts()}
    print(json.dumps(result,sort_keys=True,separators=(",",":"))); return 0

if __name__=="__main__": raise SystemExit(main())
