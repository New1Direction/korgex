"""Hosted shareable receipts — publish a proof page to a Pages repo, get a real URL.

`korgex receipt share <file> --publish` writes the self-verifying page into a configured
static-site checkout (`KORGEX_SHARE_PAGES_REPO`) under `r/<id>.html` and git-pushes it,
returning a public link like `https://yvaehkorg.lol/r/<id>.html` — which unfurls as a
proof card (real HTML hosting) and re-verifies in the recipient's browser. The id is the
receipt tip (content-addressed → same receipt = same stable URL). This closes the viral
loop: run → publish → share a link → a stranger verifies with zero trust.
"""
import subprocess

from src import share_publish as SP
from src import receipt as RC
from src import korg_ledger as KL


def _receipt(tmp_path):
    jp = str(tmp_path / "j.jsonl")
    c = KL.LocalJournalClient(journal_path=jp)
    root = c.record_user_prompt("ship the thing")
    c.record_tool_call("Edit", {"file_path": "a.py"}, {"ok": True}, True, 5, triggered_by=root)
    return RC.build_receipt(KL.load_journal_raw(jp), claim="shipped", generated_at=1.0)


def test_publish_writes_page_under_r_with_a_stable_content_addressed_id(tmp_path):
    rec = _receipt(tmp_path)
    repo = tmp_path / "site"
    repo.mkdir()
    res = SP.publish_receipt(rec, repo_dir=str(repo), base_url="https://yvaehkorg.lol")

    short = rec["tip"][:12]
    assert res["id"] == short                                    # content-addressed by the tip
    assert res["url"] == f"https://yvaehkorg.lol/r/{short}.html"
    page = repo / "r" / f"{short}.html"
    assert page.exists()
    html = page.read_text()
    assert "verifyChain" in html                                 # still self-verifying in-browser
    assert res["url"] in html                                    # og:url canonical = the public link
    # idempotent: same receipt → same url/path (a re-publish overwrites, doesn't duplicate)
    again = SP.publish_receipt(rec, repo_dir=str(repo), base_url="https://yvaehkorg.lol")
    assert again["url"] == res["url"]


def test_published_page_carries_a_social_card_at_the_public_url(tmp_path):
    rec = _receipt(tmp_path)
    repo = tmp_path / "site"
    repo.mkdir()
    SP.publish_receipt(rec, repo_dir=str(repo), base_url="https://x.io")
    html = (repo / "r" / f"{rec['tip'][:12]}.html").read_text()
    assert 'property="og:url"' in html and "https://x.io/r/" in html
    assert 'property="og:image"' in html                         # unfurls as a card


def test_base_url_trailing_slash_is_normalized(tmp_path):
    rec = _receipt(tmp_path)
    repo = tmp_path / "site"
    repo.mkdir()
    res = SP.publish_receipt(rec, repo_dir=str(repo), base_url="https://yvaehkorg.lol/")
    assert "//r/" not in res["url"].replace("https://", "")      # no double slash


def test_git_deploy_commits_and_pushes_to_a_local_remote(tmp_path):
    # a bare repo as the "remote", a working clone as the Pages checkout — no network
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True, capture_output=True)

    (work / "r").mkdir()
    (work / "r" / "abc.html").write_text("<html>proof</html>")
    assert SP.git_deploy(str(work), "r/abc.html", "publish receipt abc") is True

    log = subprocess.run(["git", "-C", str(bare), "log", "--oneline"], capture_output=True, text=True)
    assert "publish receipt abc" in log.stdout                   # the file reached the remote


def test_git_deploy_is_best_effort_on_a_non_repo(tmp_path):
    plain = tmp_path / "notarepo"
    plain.mkdir()
    (plain / "x.html").write_text("hi")
    assert SP.git_deploy(str(plain), "x.html", "msg") is False    # no git repo → False, never raises
