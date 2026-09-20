from __future__ import annotations

import io
import unittest

from jev_skill_advisor.notion_import import NotionExportClient, NotionImportError


class _Response:
    def __init__(self, body=b"archive", url="https://files.example/archive"):
        self.body=io.BytesIO(body); self.url=url
    def read(self, size=-1): return self.body.read(size)
    def geturl(self): return self.url


class _Opener:
    def __init__(self, response): self.response=response; self.request=None
    def open(self, request, timeout): self.request=request; return self.response


class NotionImportTests(unittest.TestCase):
    def test_fetch_uses_no_authorization_header(self):
        opener=_Opener(_Response())
        client=NotionExportClient(runner=lambda path:{"id":"page-1","version_id":"a"*64,"url":"https://files.example/archive"},opener=opener)
        result=client.fetch("skill","page-1")
        self.assertEqual(result.archive,b"archive")
        self.assertNotIn("Authorization",opener.request.headers)

    def test_identity_and_https_are_required(self):
        mismatch=NotionExportClient(runner=lambda path:{"id":"other","version_id":"a"*64,"url":"https://files.example/a"})
        with self.assertRaisesRegex(NotionImportError,"identity_mismatch"): mismatch.inspect("skill","page-1")
        insecure=NotionExportClient(runner=lambda path:{"id":"page-1","version_id":"a"*64,"url":"http://files.example/a"})
        with self.assertRaisesRegex(NotionImportError,"invalid_notion_export"): insecure.inspect("skill","page-1")

    def test_expired_or_oversized_download_fails(self):
        runner=lambda path:{"id":"page-1","version_id":"a"*64,"url":"https://files.example/a"}
        expired=NotionExportClient(runner=runner,opener=_Opener(RuntimeError("expired")))
        with self.assertRaisesRegex(NotionImportError,"download_failure"): expired.fetch("skill","page-1")
        oversized=NotionExportClient(runner=runner,opener=_Opener(_Response(b"12345")),max_archive_bytes=4)
        with self.assertRaisesRegex(NotionImportError,"archive_too_large"): oversized.fetch("skill","page-1")


if __name__ == "__main__": unittest.main()
