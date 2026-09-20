from __future__ import annotations

import io
import json
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

from jev_skill_advisor.catalog_cli import build_catalog
from jev_skill_advisor.library_cache import LibraryCache, LibraryCacheError, _archive_files
from jev_skill_advisor.notion_import import Export


def archive(files, *, link=None, modes=None):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode="w:gz") as bundle:
        for name,data in files.items():
            info=tarfile.TarInfo(name); info.size=len(data); info.mode=(modes or {}).get(name,0o644); bundle.addfile(info,io.BytesIO(data))
        if link:
            info=tarfile.TarInfo(link); info.type=tarfile.SYMTYPE; info.linkname="/tmp/out"; bundle.addfile(info)
    return output.getvalue()


def export(index, body=None):
    name=f"skill-{index}"
    files={f"package/{name}/SKILL.md":body or f"---\nname: {name}\ndescription: Pilot {index}.\n---\n".encode(),
           f"package/{name}/references/info.txt":f"reference-{index}".encode()}
    return Export("skill",f"page-{index}",f"{index:064x}",archive(files))


class LibraryCacheTests(unittest.TestCase):
    def test_deterministic_snapshot_attachment_fidelity_and_offline_status(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache")
            first=cache.publish([export(1),export(2),export(3)])
            second=cache.publish([export(3),export(1),export(2)])
            self.assertEqual(first["snapshot_id"],second["snapshot_id"])
            root=Path(second["catalog_root"])
            self.assertEqual((root/"skill-2"/"references"/"info.txt").read_text(),"reference-2")
            catalog=build_catalog(root)
            self.assertEqual(catalog["included_count"],3)
            self.assertEqual(catalog["excluded_count"],0)
            self.assertEqual(LibraryCache(cache.root).status(),second)

    def test_update_is_recoverable_and_rollback_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache")
            first=cache.publish([export(1),export(2),export(3)])
            changed=export(1,b"---\nname: skill-1\ndescription: Changed.\n---\n")
            changed=Export(changed.kind,changed.id,"f"*64,changed.archive)
            second=cache.publish([changed,export(2),export(3)])
            self.assertNotEqual(first["snapshot_id"],second["snapshot_id"])
            restored=cache.rollback(first["snapshot_id"])
            self.assertEqual(restored["snapshot_id"],first["snapshot_id"])

    def test_failed_import_preserves_current_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache"); first=cache.publish([export(1),export(2),export(3)])
            bad=Export("skill","bad","b"*64,archive({"../escape/SKILL.md":b"bad"}))
            with self.assertRaisesRegex(LibraryCacheError,"unsafe_archive_path"): cache.publish([export(1),export(2),bad])
            self.assertEqual(cache.status()["snapshot_id"],first["snapshot_id"])

    def test_links_missing_skill_duplicates_and_configured_bounds_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache")
            linked=Export("skill","linked","c"*64,archive({"p/a/SKILL.md":b"x"},link="p/a/link"))
            with self.assertRaisesRegex(LibraryCacheError,"unsupported_archive_entry"): cache.publish([export(1),export(2),linked])
            missing=Export("skill","missing","d"*64,archive({"p/a/readme.txt":b"x"}))
            with self.assertRaisesRegex(LibraryCacheError,"missing_skill_md"): cache.publish([export(1),export(2),missing])
            with self.assertRaisesRegex(LibraryCacheError,"duplicate_export"): cache.publish([export(1),export(1),export(2)])
            bounded=LibraryCache(Path(directory)/"bounded",max_exports=2,max_skills=2)
            with self.assertRaisesRegex(LibraryCacheError,"export_count_out_of_bounds"): bounded.publish([export(1),export(2),export(3)])

    def test_same_version_with_changed_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache"); first=cache.publish([export(1),export(2),export(3)])
            changed=export(1,b"---\nname: skill-1\ndescription: Tampered.\n---\n")
            changed=Export(changed.kind,changed.id,export(1).version_id,changed.archive)
            with self.assertRaisesRegex(LibraryCacheError,"content_changed_without_version_change"):
                cache.publish([changed,export(2),export(3)])
            self.assertEqual(cache.status()["snapshot_id"],first["snapshot_id"])

    def test_manifest_contains_no_url_or_token(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache"); status=cache.publish([export(1),export(2),export(3)])
            manifest=(cache.snapshots/status["snapshot_id"]/"manifest.json").read_text()
            self.assertNotIn("url",manifest.lower()); self.assertNotIn("token",manifest.lower())
            parsed=json.loads(manifest); self.assertEqual(parsed["skill_count"],3)

    def test_tampering_and_traversal_snapshot_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache"); ready=cache.publish([export(1),export(2),export(3)])
            target=Path(ready["catalog_root"])/"skill-1"/"SKILL.md"; target.write_text("tampered")
            with self.assertRaisesRegex(LibraryCacheError,"snapshot_content_mismatch"): cache.status()
            with self.assertRaisesRegex(LibraryCacheError,"invalid_snapshot_id"): cache.rollback("../../external")

    def test_case_collisions_across_exports_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache")
            upper=Export("skill","upper","a"*64,archive({"p/Alpha/SKILL.md":b"---\nname: Alpha\ndescription: Upper.\n---\n"}))
            lower=Export("skill","lower","b"*64,archive({"p/alpha/SKILL.md":b"---\nname: alpha\ndescription: Lower.\n---\n"}))
            with self.assertRaisesRegex(LibraryCacheError,"duplicate_skill_destination"):
                cache.publish([upper,lower,export(3)])

    def test_directory_case_collision_leaves_cache_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache")
            collision=Export("skill","case","c"*64,archive({
                "p/skill-case/SKILL.md":b"---\nname: skill-case\ndescription: Case test.\n---\n",
                "p/skill-case/Refs/a.txt":b"a", "p/skill-case/refs/b.txt":b"b"}))
            with self.assertRaisesRegex(LibraryCacheError,"case_colliding_directory"):
                cache.publish([collision,export(2),export(3)])
            self.assertEqual(cache.status()["status"],"empty")
            self.assertEqual(cache.publish([export(1),export(2),export(3)])["status"],"ready")

    def test_entry_and_expansion_limits_are_incremental(self):
        many=Export("skill","many","a"*64,archive({f"p/a/{i}.txt":b"x" for i in range(4)}))
        with self.assertRaisesRegex(LibraryCacheError,"too_many_archive_entries"):
            _archive_files(many,max_files=2,max_expanded_bytes=100_000)
        large=Export("skill","large","b"*64,archive({"p/a/SKILL.md":b"x"*100}))
        with self.assertRaisesRegex(LibraryCacheError,"expanded_archive_too_large"):
            _archive_files(large,max_files=10,max_expanded_bytes=50)

    def test_concurrent_same_version_conflict_serializes(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=LibraryCache(Path(directory)/"cache"); barrier=threading.Barrier(2); outcomes=[]
            first=export(1); changed=export(1,b"---\nname: skill-1\ndescription: Other.\n---\n")
            def run(candidate):
                barrier.wait()
                try: outcomes.append(("ok",cache.publish([candidate,export(2),export(3)])["snapshot_id"]))
                except LibraryCacheError as exc: outcomes.append(("error",str(exc)))
            threads=[threading.Thread(target=run,args=(candidate,)) for candidate in (first,changed)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual([kind for kind,_ in outcomes].count("ok"),1)
            self.assertEqual([value for kind,value in outcomes if kind=="error"],["content_changed_without_version_change"])
            self.assertEqual(cache.status()["status"],"ready")

    def test_versioned_package_manifest_nested_roots_and_executable_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            package={"schema_version":1,"stable_id":"shared:outer","aliases":["warehouse:old-outer"],
                     "entrypoint":"SKILL.md","invocation_policy":"implicit","required_runtimes":["python3"],
                     "executable_paths":["scripts/check.sh"]}
            nested={"schema_version":1,"stable_id":"shared:nested","entrypoint":"SKILL.md",
                    "invocation_policy":"explicit","required_runtimes":[]}
            files={"bundle/outer/SKILL.md":b"---\nname: outer\ndescription: Outer.\n---\nSee scripts/check.sh\n",
                   "bundle/outer/skill-package.json":json.dumps(package).encode(),
                   "bundle/outer/scripts/check.sh":b"#!/bin/sh\nprintf package-ok\n",
                   "bundle/outer/nested/SKILL.md":b"---\nname: nested\ndescription: Nested.\n---\n",
                   "bundle/outer/nested/skill-package.json":json.dumps(nested).encode()}
            payload=Export("plugin","plugin-1","a"*64,archive(files,modes={"bundle/outer/scripts/check.sh":0o755}))
            status=LibraryCache(Path(directory)/"cache").publish([payload])
            root=Path(status["catalog_root"]); manifest=json.loads((root.parent/"manifest.json").read_text())
            self.assertEqual({row["stable_id"] for row in manifest["packages"]},{"shared:outer","shared:nested"})
            destinations={row["stable_id"]:row["destination"] for row in manifest["packages"]}
            outer=root/destinations["shared:outer"]; nested=root/destinations["shared:nested"]
            self.assertEqual((outer/"scripts"/"check.sh").read_bytes(),files["bundle/outer/scripts/check.sh"])
            self.assertTrue((outer/"scripts"/"check.sh").stat().st_mode & 0o111)
            self.assertEqual((outer/"nested"/"SKILL.md").read_bytes(),files["bundle/outer/nested/SKILL.md"])
            self.assertEqual((nested/"SKILL.md").read_bytes(),files["bundle/outer/nested/SKILL.md"])

    def test_explicit_stable_identity_survives_source_directory_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest={"schema_version":1,"stable_id":"shared:stable","entrypoint":"SKILL.md",
                      "invocation_policy":"implicit"}
            def item(folder,version):
                files={f"p/{folder}/SKILL.md":b"---\nname: stable\ndescription: Stable.\n---\n",
                       f"p/{folder}/skill-package.json":json.dumps(manifest).encode()}
                return Export("skill","page-stable",version,archive(files))
            cache=LibraryCache(Path(directory)/"cache")
            first=cache.publish([item("before","a"*64)])
            second=cache.publish([item("after","b"*64)])
            for snapshot in (first,second):
                data=json.loads((cache.snapshots/snapshot["snapshot_id"]/"manifest.json").read_text())
                self.assertEqual(data["packages"][0]["stable_id"],"shared:stable")


if __name__ == "__main__": unittest.main()
