from git import Repo
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class GitManager:
    """Manages Git repository operations and diff extraction"""

    def __init__(self, repo_path: str):
        """
        Initialize GitManager with repository path.

        Args:
            repo_path: Path to the Git repository
        """
        try:
            self.repo = Repo(repo_path)
            if self.repo.bare:
                raise ValueError(f"Repository at {repo_path} is bare")
        except Exception as e:
            logger.error(f"Failed to initialize repository at {repo_path}: {e}")
            raise

    def get_staged_changes(self) -> List[Dict[str, str]]:
        """
        Get all staged changes (changes in index compared to HEAD).

        Returns:
            List of dicts with 'path' and 'diff' keys
        """
        try:
            diffs = self.repo.index.diff("HEAD")
            changed_files = []
            for d in diffs:
                diff_text = d.diff.decode("utf-8") if d.diff else ""
                changed_files.append({
                    "path": d.a_path or d.b_path,
                    "diff": diff_text,
                    "change_type": d.change_type
                })
            return changed_files
        except Exception as e:
            logger.error(f"Failed to get staged changes: {e}")
            return []

    def get_unstaged_changes(self) -> List[Dict[str, str]]:
        """
        Get all unstaged changes (changes in working directory compared to index).

        Returns:
            List of dicts with 'path' and 'diff' keys
        """
        try:
            diffs = self.repo.index.diff(None)
            changed_files = []
            for d in diffs:
                diff_text = d.diff.decode("utf-8") if d.diff else ""
                changed_files.append({
                    "path": d.a_path or d.b_path,
                    "diff": diff_text,
                    "change_type": d.change_type
                })
            return changed_files
        except Exception as e:
            logger.error(f"Failed to get unstaged changes: {e}")
            return []

    def get_all_changes(self) -> List[Dict[str, str]]:
        """
        Get all changes (both staged and unstaged).

        Returns:
            List of dicts with 'path', 'diff', and 'staged' keys
        """
        changes = []

        # Get staged changes
        staged = self.get_staged_changes()
        for change in staged:
            change['staged'] = True
            changes.append(change)

        # Get unstaged changes
        unstaged = self.get_unstaged_changes()
        for change in unstaged:
            change['staged'] = False
            changes.append(change)

        return changes

    def get_commit_diff(self, commit_sha: str, parent_sha: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Get diff for a specific commit.

        Args:
            commit_sha: SHA of the commit to get diff for
            parent_sha: Optional parent SHA to compare against (defaults to commit's first parent)

        Returns:
            List of dicts with 'path' and 'diff' keys
        """
        try:
            commit = self.repo.commit(commit_sha)

            if parent_sha:
                parent = self.repo.commit(parent_sha)
            elif commit.parents:
                parent = commit.parents[0]
            else:
                # Initial commit - compare against empty tree
                parent = None

            if parent:
                diffs = parent.diff(commit)
            else:
                diffs = commit.diff(self.repo.tree('4b825dc642cb6eb9a060e54bf8d69288fbee4904'))  # Empty tree SHA

            changed_files = []
            for d in diffs:
                diff_text = d.diff.decode("utf-8") if d.diff else ""
                changed_files.append({
                    "path": d.a_path or d.b_path,
                    "diff": diff_text,
                    "change_type": d.change_type
                })
            return changed_files
        except Exception as e:
            logger.error(f"Failed to get commit diff for {commit_sha}: {e}")
            return []

    def get_branch_diff(self, base_branch: str = "main", compare_branch: str = "HEAD") -> List[Dict[str, str]]:
        """
        Get diff between two branches.

        Args:
            base_branch: Base branch name (default: "main")
            compare_branch: Branch to compare (default: "HEAD")

        Returns:
            List of dicts with 'path' and 'diff' keys
        """
        try:
            base = self.repo.commit(base_branch)
            compare = self.repo.commit(compare_branch)

            diffs = base.diff(compare)
            changed_files = []
            for d in diffs:
                diff_text = d.diff.decode("utf-8") if d.diff else ""
                changed_files.append({
                    "path": d.a_path or d.b_path,
                    "diff": diff_text,
                    "change_type": d.change_type
                })
            return changed_files
        except Exception as e:
            logger.error(f"Failed to get branch diff between {base_branch} and {compare_branch}: {e}")
            return []

    def get_file_content(self, file_path: str, ref: str = "HEAD") -> Optional[str]:
        """
        Get content of a file at a specific ref.

        Args:
            file_path: Path to the file
            ref: Git ref (commit SHA, branch name, etc.) - default: "HEAD"

        Returns:
            File content as string, or None if not found
        """
        try:
            commit = self.repo.commit(ref)
            blob = commit.tree / file_path
            return blob.data_stream.read().decode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to get content for {file_path} at {ref}: {e}")
            return None

    def get_current_branch(self) -> str:
        """Get name of current branch"""
        try:
            return self.repo.active_branch.name
        except Exception as e:
            logger.warning(f"Failed to get current branch: {e}")
            return "HEAD"

    def is_repo_dirty(self) -> bool:
        """Check if repository has uncommitted changes"""
        return self.repo.is_dirty()