import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publish_updates as publisher


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="wts-publish-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "scraper"
        self.other = self.root / "other-workflow"
        self.run_git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.run_git(self.root, "clone", str(self.remote), str(self.repo))
        self.write(self.repo / "state.json", {})
        self.write(self.repo / "accesorios.json", {"products": []})
        (self.repo / "producto.html").write_text('<html><head><title>Producto</title></head><body><div id="product-detail"></div><script src="/producto.js"></script></body></html>', encoding="utf-8")
        (self.repo / "sitemap.xml").write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://wheresthatstock.com/</loc><lastmod>2026-01-01</lastmod></url></urlset>', encoding="utf-8")
        self.commit(self.repo, "seed")
        self.run_git(self.repo, "push", "origin", "main")
        self.run_git(self.root, "clone", str(self.remote), str(self.other))
        self.print_patch = patch("builtins.print")
        self.print_patch.start()
        self.addCleanup(self.print_patch.stop)

    def run_git(self, repo, *args):
        return publisher.git(repo, *args).stdout.strip()

    def commit(self, repo, message):
        self.run_git(repo, "add", ".")
        self.run_git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", message)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def remote_file(self, name):
        return json.loads(self.run_git(self.remote, "show", f"main:{name}"))

    def assert_no_extra_worktrees(self):
        lines = self.run_git(self.repo, "worktree", "list", "--porcelain").splitlines()
        self.assertEqual(sum(line.startswith("worktree ") for line in lines), 1)

    def test_publishes_state_without_changing_the_scraper_checkout(self):
        head = self.run_git(self.repo, "rev-parse", "HEAD")
        self.write(self.repo / "state.json", {"sent": True})
        (self.other / "unrelated.txt").write_text("Another workflow's change", encoding="utf-8")
        self.commit(self.other, "unrelated")
        self.run_git(self.other, "push", "origin", "main")
        apply = publisher.prepare_updates([(self.repo / "state.json", "state.json")])
        self.assertTrue(publisher.publish(self.repo, apply, "state"))
        self.assertEqual(self.remote_file("state.json"), {"sent": True})
        self.assertEqual(self.run_git(self.remote, "show", "main:unrelated.txt"), "Another workflow's change")
        self.assertEqual(self.run_git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(json.loads((self.repo / "state.json").read_text()), {"sent": True})
        self.assert_no_extra_worktrees()

    def test_retry_remerges_accessories_and_sitemap_after_a_real_push_conflict(self):
        new = self.root / "accessories-new.json"
        generic = {"asin": "B000000001", "marketplace": "ES", "categories": ["Sin asociar"], "price": "8,00 €"}
        onepiece = {"asin": "B000000002", "marketplace": "ES", "categories": ["One Piece"], "source": "onepiece", "price": "12,00 €"}
        self.write(new, {"updated_at": "2026-09-08T08:00:00Z", "products": [generic]})
        apply = publisher.prepare_updates([], accessories=new, source="accessories", sitemap=True)
        real_git = publisher.git
        pushes = []

        def competing_push(repo, *args, **kwargs):
            if args and args[0] == "push" and Path(repo) != self.other:
                pushes.append(1)
                if len(pushes) == 1:
                    self.write(self.other / "accesorios.json", {"products": [onepiece]})
                    path = self.other / "sitemap.xml"
                    path.write_text(path.read_text().replace("</urlset>", "<url><loc>https://wheresthatstock.com/new-article</loc></url></urlset>"), encoding="utf-8")
                    self.commit(self.other, "concurrent One Piece update")
                    real_git(self.other, "push", "origin", "main")
            return real_git(repo, *args, **kwargs)

        with patch.object(publisher, "git", side_effect=competing_push):
            self.assertTrue(publisher.publish(self.repo, apply, "accessories"))
        self.assertEqual(len(pushes), 2)
        products = self.remote_file("accesorios.json")["products"]
        self.assertEqual({p["asin"]: p["price"] for p in products}, {"B000000001": "8,00 €", "B000000002": "12,00 €"})
        self.assertIn("new-article", self.run_git(self.remote, "show", "main:sitemap-core.xml"))
        self.assert_no_extra_worktrees()

    def test_three_failed_pushes_raise_and_leave_remote_unchanged(self):
        initial = self.run_git(self.remote, "rev-parse", "main")
        self.write(self.repo / "state.json", {"sent": True})
        apply = publisher.prepare_updates([(self.repo / "state.json", "state.json")])
        real_git = publisher.git
        pushes = []

        def rejected_push(repo, *args, **kwargs):
            if args[0] == "push":
                pushes.append(1)
                return subprocess.CompletedProcess(args, 1, "", "Rejected")
            return real_git(repo, *args, **kwargs)

        with patch.object(publisher, "git", side_effect=rejected_push):
            with self.assertRaises(publisher.PublicationError):
                publisher.publish(self.repo, apply, "state")
        self.assertEqual(len(pushes), 3)
        self.assertEqual(self.run_git(self.remote, "rev-parse", "main"), initial)
        self.assert_no_extra_worktrees()

    def test_republishing_identical_data_does_not_create_a_commit(self):
        initial = self.run_git(self.remote, "rev-parse", "main")
        apply = publisher.prepare_updates([(self.repo / "state.json", "state.json")])
        self.assertFalse(publisher.publish(self.repo, apply, "state"))
        self.assertEqual(self.run_git(self.remote, "rev-parse", "main"), initial)
        self.assert_no_extra_worktrees()

    def test_failed_web_publication_does_not_undo_persisted_state(self):
        self.write(self.repo / "state.json", {"sent": True})
        apply = publisher.prepare_updates([(self.repo / "state.json", "state.json")])
        publisher.publish(self.repo, apply, "state")
        with self.assertRaises(FileNotFoundError):
            publisher.prepare_updates([(self.root / "missing-web-snapshot.json", "products.json")])
        self.assertEqual(self.remote_file("state.json"), {"sent": True})

    def test_destinations_cannot_escape_the_temporary_checkout(self):
        for name in ("../outside.json", ".git/config", str(self.root / "outside.json")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                publisher.target_path(self.repo.resolve(), name)


class WorkflowOrderTests(unittest.TestCase):
    def test_all_six_workflows_persist_state_before_web_publication(self):
        folder = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for game in ("stock", "onepiece", "magic", "lorcana", "yugioh", "accessories"):
            with self.subTest(game=game):
                workflow = (folder / f"check_{game}.yml").read_text(encoding="utf-8")
                self.assertLess(workflow.index("- name: Commit updated state"), workflow.index("- name: Publish products snapshot"))
                self.assertEqual(workflow.count("python publish_updates.py"), 2)
                self.assertNotIn("git pull --rebase", workflow)
                if game == "stock":
                    self.assertLess(workflow.index("- name: Commit updated state"), workflow.index("- name: Compute restock stats"))


if __name__ == "__main__":
    unittest.main()
