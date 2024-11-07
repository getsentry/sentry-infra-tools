## 0.0.20

### Various fixes & improvements

- chore(service-registry): Add python package import solution (#56) by @brian-lou

## 0.0.19

### Various fixes & improvements

- Use own jsonpatch implementation (#55) by @brian-lou
- Add the gcloud CLI to the devenev setup (#57) by @fpacifici
- Wire up patches into sentry-kube quickpatch (#52) by @fpacifici
- [pre-commit.ci] pre-commit autoupdate (#54) by @pre-commit-ci
- feat(git): Allow git operations (#50) by @nikhars

## 0.0.18

### Various fixes & improvements

- feat(quickpatch): Add apply patch module (#46) by @brian-lou
- Introduce an added override file to be managed by tools (#49) by @fpacifici

## 0.0.17

### Various fixes & improvements

- Add quickpatch tool scaffolding (#45) by @fpacifici

## 0.0.16

### Various fixes & improvements

- Update min Python version and sentry-jsonnet dependency version (#44) by @brian-lou
- [pre-commit.ci] pre-commit autoupdate (#43) by @pre-commit-ci
- chore(formatting): Fix formatting of all code (#42) by @nikhars

## 0.0.15

### Various fixes & improvements

- feat: Initial support for alternative kubectls (#40) by @rgibert

## 0.0.14

### Various fixes & improvements

- fix(service-registry): Use the correct path to find JSON file (#41) by @nikhars

## 0.0.13

### Various fixes & improvements

- fix: add missing __init__.py for kubectl (#38) by @bmckerry
- fix(service_registry): Dynamic detection of path (#33) by @nikhars
- feat(devenv): Use devenv for setup (#36) by @nikhars
- chore(readme): sentry-infra-tools in editable mode (#34) by @nikhars

## 0.0.12

### Various fixes & improvements

- fix(diff): Expose important-diffs-only (#37) by @nikhars

## 0.0.11

### Various fixes & improvements

- Sync till 134da6c507d2ee342575762cb3e47cc25b898767 (#35) by @nikhars

## 0.0.10

### Various fixes & improvements

- chore(sync): Sync changes till a864cafd6a4771434bab51c79b7773877c8342d7 (#32) by @nikhars

## 0.0.9

### Various fixes & improvements

- chore(sync): Sync with changes till sha b65df3da50166daec95facef9ca44d8ebfed525 (#31) by @nikhars
- Install all dependencies (#30) by @nikhars
- Fix the dependencies (#30) by @nikhars
- fix (#30) by @nikhars
- ignore dist directory (#30) by @nikhars
- fix the dependencies (#30) by @nikhars
- Sync upto sha 493ddf7e9b5ed3a5e5ee8233ea3fd330c3bb3c7 (#27) by @nikhars
- Remove sentry kafka schemas dependency (#29) by @nikhars
- Remove it from setup.py (#29) by @nikhars
- Remove generate topic data (#29) by @nikhars
- WIP (#30) by @nikhars
- chore(sync): Sync with changes on ops repo (#27) by @nikhars

## 0.0.8

### Various fixes & improvements

- fix(materialize): Take root directory as argument (#26) by @nikhars
- Add typing to packages (#25) by @nikhars

## 0.0.7

### Various fixes & improvements

- Bring back requirements.in files (#23) by @nikhars
- Revert "Remove .in files with dependencies" (#23) by @nikhars
- Fix make target (#24) by @nikhars
- Fix make command (#24) by @nikhars
- Use make command in GHA (#24) by @nikhars
- Add sentry pypi for dev requirements (#23) by @nikhars
- Remove pyright (#23) by @nikhars
- Revert "Does removing pyright still work" (#24) by @nikhars
- Does removing pyright still work (#24) by @nikhars
- Remove mypy from k8s folder (#24) by @nikhars
- More fixes to GHA (#24) by @nikhars
- Fix makefile target (#24) by @nikhars
- fix(mypy): Add mypy github action (#24) by @nikhars
- Add requirements file to make target (#23) by @nikhars
- Remove comments from requirements file (#23) by @nikhars
- Remove .in files with dependencies (#23) by @nikhars
- Add build to gitignore (#22) by @nikhars
- Remove build directory (#22) by @nikhars
- chore(requirements): Add sentry pypi index url (#23) by @nikhars

## 0.0.6

### Various fixes & improvements

- chore(requirements): Fix dependencies based on sentry pypi (#21) by @nikhars

## 0.0.5

### Various fixes & improvements

- chore(review): Add CODEOWNERS (#20) by @nikhars
- fix(python): Reduce minimum python version required to 3.10 (#19) by @nikhars

## 0.0.4

### Various fixes & improvements

- fix(keys): Remove default datadog key (#18) by @nikhars
- release: 0.0.2 (#17) by @nikhars
- fix(craft): Remove unnecessary fields (#16) by @nikhars
- fix(release): Add changelog file for release process to work (#14) by @nikhars

## 0.0.2

### Various fixes & improvements

- fix(craft): Remove unnecessary fields (#16) by @nikhars
- fix(release): Add changelog file for release process to work (#14) by @nikhars

## 2024-09-09

- Merge pull request #13 from getsentry/nikhars/feat/bump-version-script (81dc0b1)
## 2024-09-09

- Merge pull request #12 from getsentry/nikhars/feat/craft-add-statusprovider (37efab4)
## 2024-09-09

- feat(release): Add bump version script to automate version updates (44444e7)
## 2024-09-09

- fix(craft): Add more metadata to check if release is successful (a998e1a)
## 2024-09-09

- Merge pull request #11 from getsentry/nikhars/feat/add-craft (cbc9ab0)
## 2024-09-09

- feat(craft): Add craft for releasing the package (30db5e9)
## 2024-09-09

- Merge pull request #10 from getsentry/nikhars/feat/build-and-release (1d5f684)
## 2024-09-09

- feat(release): Build and release (acede8d)
## 2024-09-09

- Merge pull request #9 from getsentry/nikhars/feat/make-package (bfe27ff)
## 2024-09-05

- add libsentrykube macros (dde0712)
## 2024-09-05

- Move libsenrtrykube one level up (fa05072)
## 2024-09-05

- partial fix to setup.py (acd66ec)
## 2024-09-04

- Merge pull request #8 from getsentry/nikhars/feat/sentry-kube-ci.yaml (4c3891e)
## 2024-09-04

- Merge branch 'main' into nikhars/feat/sentry-kube-ci.yaml (029654e)
## 2024-09-04

- Fix command (96d3592)
## 2024-09-04

- feat(gh): Add gha to run tests (8e409cc)
## 2024-09-04

- Merge pull request #7 from getsentry/nikhars/feat/default-config (4699ec8)
## 2024-09-04

- feat(tests): Make all existing tests pass (869feb7)
## 2024-09-04

- Merge pull request #6 from getsentry/nikhars/feat/sync-with-c292a414d8ca1cb5c3137b36c54fc03f9ab8b105 (ebeaab9)
## 2024-09-04

- chore: Apply diff between sha 1122b46cc4566cd755d1475511807e9334fd04f9 and c292a414d8ca1cb5c3137b36c54fc03f9ab8b105 (7051f7d)
## 2024-09-03

- Merge pull request #4 from getsentry/nikhars/feat/venv-setup (a9cfe7b)
## 2024-09-03

- Fix pre commit hook (f16256d)
## 2024-09-03

- Merge pull request #3 from getsentry/nikhars/feat/pin-python-version (7b6c530)
## 2024-09-03

- Pin python version to 3.11.9 (09767fe)
## 2024-09-03

- Merge pull request #2 from getsentry/nikhars/feat/secret-scanner-delete (ab24934)
## 2024-09-03

- Remove local secret scanning (41fa35b)
## 2024-09-03

- Merge pull request #1 from getsentry/nikhars/feat/secret-scanner (41459de)
## 2024-09-03

- Add secret scanning (7b94c49)
## 2024-08-29

- Initial commit (4d789b0)
