import git


class RepoNotCleanException(Exception):
    pass


class Git:
    def __init__(self, repo_path: str):
        self.repo = git.Repo(repo_path)
        self.__heads = [head.name for head in self.repo.heads]
        if "main" in self.__heads:
            self.default_branch = "main"
        else:
            self.default_branch = "master"

    def switch_to_default_branch(self, *, force: bool) -> None:
        if self.repo.active_branch.name == self.default_branch:
            return

        # check if there are any changes
        if self.repo.is_dirty() and not force:
            raise RepoNotCleanException

        self.stash()

        self.switch_to_branch(self.default_branch)

    def stash(self) -> None:
        self.repo.git.stash()
        self.stashed = True

    def pop_stash(self) -> None:
        if not self.stashed:
            return

        self.repo.git.stash("pop")
        self.stashed = False

    def pull_latest_changes(self) -> None:
        self.repo.git.pull()

    def add(self, files: list[str]) -> None:
        self.repo.index.add(files)

    def commit(self, message: str) -> None:
        self.repo.index.commit(message)

    def create_branch(self, branch_name: str) -> None:
        self.repo.create_head(branch_name, commit=self.default_branch)

    def switch_to_branch(self, branch_name: str) -> None:
        branch = next(head for head in self.repo.heads if head.name == branch_name)
        branch.checkout()
