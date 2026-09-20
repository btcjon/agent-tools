from __future__ import annotations
import argparse,json
from pathlib import Path
from .notion_sync import discover_remote,execute_sync,fidelity_preflight,freeze_inventory,plan_sync,verify_remote_receipts


def main(argv=None):
    parser=argparse.ArgumentParser(prog="skill-library-sync"); commands=parser.add_subparsers(dest="command",required=True)
    freeze=commands.add_parser("inventory"); freeze.add_argument("--warehouse",type=Path,required=True); freeze.add_argument("--output",type=Path,required=True); freeze.add_argument("--shard-size",type=int,default=80)
    plan=commands.add_parser("plan"); plan.add_argument("--inventory",type=Path,required=True); plan.add_argument("--remote",type=Path,required=True); plan.add_argument("--database-id",required=True); plan.add_argument("--data-source-id",required=True); plan.add_argument("--output",type=Path,required=True)
    sync=commands.add_parser("sync"); sync.add_argument("--inventory",type=Path,required=True); sync.add_argument("--plan",type=Path,required=True); sync.add_argument("--ledger",type=Path,required=True); sync.add_argument("--run-id"); sync.add_argument("--limit",type=int); sync.add_argument("--only-id",action="append"); sync.add_argument("--exclude-id",action="append",default=[]); sync.add_argument("--preflight",type=Path); sync.add_argument("--create-only",action="store_true")
    preflight=commands.add_parser("preflight"); preflight.add_argument("--inventory",type=Path,required=True); preflight.add_argument("--output",type=Path,required=True)
    remote=commands.add_parser("remote"); remote.add_argument("--data-source-id",required=True); remote.add_argument("--bootstrap",type=Path); remote.add_argument("--inventory",type=Path); remote.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.command=="inventory": result=freeze_inventory(args.warehouse,shard_size=args.shard_size)
    elif args.command=="plan": result=plan_sync(json.loads(args.inventory.read_text()),json.loads(args.remote.read_text()),database_id=args.database_id,data_source_id=args.data_source_id)
    elif args.command=="remote":
        result=discover_remote(args.data_source_id,bootstrap=json.loads(args.bootstrap.read_text()) if args.bootstrap else None)
        if args.inventory: result=verify_remote_receipts(json.loads(args.inventory.read_text()),result)
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n"); print(json.dumps({"managed":len(result)},sort_keys=True)); return 0
    elif args.command=="preflight": result=fidelity_preflight(json.loads(args.inventory.read_text()))
    else:
        inventory=json.loads(args.inventory.read_text()); plan_value=json.loads(args.plan.read_text()); only_ids=args.only_id
        if args.preflight:
            gate=json.loads(args.preflight.read_text())
            if gate.get("inventory_hash")!=inventory.get("inventory_hash"): raise ValueError("preflight_inventory_mismatch")
            allowed=set(gate.get("passed",[])); kinds={"create"} if args.create_only else {"create","update"}
            only_ids=[item["stable_id"] for item in plan_value["actions"] if item["action"] in kinds and item["stable_id"] in allowed]
            only_ids=[identity for identity in only_ids if identity not in set(args.exclude_id)]
            if not only_ids: raise ValueError("preflight_selection_empty")
        elif args.create_only: raise ValueError("create_only_requires_preflight")
        result=execute_sync(inventory,plan_value,ledger_path=args.ledger,run_id=args.run_id,limit=args.limit,only_ids=only_ids); print(json.dumps(result,sort_keys=True)); return 0
    args.output.parent.mkdir(parents=True,exist_ok=True); temporary=args.output.with_suffix(args.output.suffix+".tmp"); temporary.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n"); temporary.replace(args.output)
    print(json.dumps({key:result[key] for key in ("inventory_hash",) if key in result}|{key:result[key] for key in ("plan_hash","counts") if key in result},sort_keys=True)); return 0

if __name__=="__main__": raise SystemExit(main())
