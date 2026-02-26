#!/usr/bin/env python3
"""Rewrite a specific commit message in git history"""

import subprocess
import sys

def run_cmd(cmd):
    """Run a shell command and return output"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

def main():
    # The commit we want to rewrite
    target_commit = "ed6b3a080d820735ec49f7ba0542d531729037c6"
    new_message = "Update documentation and reference architecture"

    print("Starting git history rewrite...")

    # Get the parent of the target commit
    parent_cmd = f"git rev-parse {target_commit}^"
    parent, err, code = run_cmd(parent_cmd)

    if code != 0:
        print(f"Error finding parent commit: {err}")
        sys.exit(1)

    print(f"Parent commit: {parent}")

    # Get list of commits from target to HEAD
    commits_cmd = f"git rev-list --reverse {target_commit}..HEAD"
    commits_output, err, code = run_cmd(commits_cmd)

    if code != 0:
        print(f"Error getting commit list: {err}")
        sys.exit(1)

    later_commits = commits_output.split('\n') if commits_output else []
    print(f"Found {len(later_commits)} commits after target commit")

    # Get the original commit info for ed6b3a0
    author_cmd = f"git log -1 --format='%an <%ae>' {target_commit}"
    author, _, _ = run_cmd(author_cmd)

    date_cmd = f"git log -1 --format='%ad' {target_commit}"
    date, _, _ = run_cmd(date_cmd)

    print(f"Original author: {author}")
    print(f"Original date: {date}")

    # Reset to parent
    print(f"\nResetting to parent commit: {parent[:8]}...")
    reset_cmd = f"git reset --hard {parent}"
    _, err, code = run_cmd(reset_cmd)

    if code != 0:
        print(f"Error during reset: {err}")
        sys.exit(1)

    # Cherry-pick the target commit without committing
    print(f"\nCherry-picking {target_commit[:8]} changes...")
    cherry_cmd = f"git cherry-pick -n {target_commit}"
    _, err, code = run_cmd(cherry_cmd)

    if code != 0:
        print(f"Error during cherry-pick: {err}")
        sys.exit(1)

    # Commit with new message, preserving original author and date
    print(f"\nCommitting with new message...")
    commit_cmd = f"GIT_AUTHOR_DATE='{date}' GIT_COMMITTER_DATE='{date}' git commit --author='{author}' -m '{new_message}'"
    _, err, code = run_cmd(commit_cmd)

    if code != 0:
        print(f"Error during commit: {err}")
        sys.exit(1)

    # Cherry-pick remaining commits
    for i, commit in enumerate(later_commits, 1):
        if commit:  # Skip empty lines
            print(f"\nCherry-picking commit {i}/{len(later_commits)}: {commit[:8]}...")
            cherry_cmd = f"git cherry-pick {commit}"
            _, err, code = run_cmd(cherry_cmd)

            if code != 0:
                print(f"Error cherry-picking {commit[:8]}: {err}")
                print("You may need to resolve conflicts manually")
                sys.exit(1)

    print("\n✓ Git history rewrite complete!")
    print("\nNext steps:")
    print("  1. Review the new history: git log --oneline -10")
    print("  2. If everything looks good, force push: git push -f origin main")
    print("  3. If something went wrong, restore backup: git reset --hard backup-before-rewrite")

if __name__ == "__main__":
    main()
