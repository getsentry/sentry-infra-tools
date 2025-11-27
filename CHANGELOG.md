## 1.20.2

- fix(apply): Fix canary applies exit early by @rgibert in [#179](https://github.com/getsentry/sentry-infra-tools/pull/179)

## 1.20.1

### Various fixes & improvements

- Adds PodDisruptionBudget to audit. (#176) by @ellisonmarks

## 1.20.0

### Various fixes & improvements

- Adds sidecar init container for pgbouncer (#174) by @ellisonmarks

## 1.19.0

### Various fixes & improvements

- Multi-thread the k8s materialization step (#173) by @BojanOro

## 1.18.0

### Various fixes & improvements

- feat(helm): add rollback cmd wrappers (#172) by @gi0baro
- release: 1.17.4 (c76ae275) by @getsentry-bot

## 1.17.4

- No documented changes.

## 1.17.3

### Various fixes & improvements

- fix: Add namespace to yaml sorting (#170) by @rgibert

## 1.17.2

### Various fixes & improvements

- fix: add sorting logic to fix over-materialization (#169) by @rgibert

## 1.17.1

### Various fixes & improvements

- feat: add include_raw jinja global (#168) by @bmckerry

## 1.17.0

### Various fixes & improvements

- feat: break out rendered files per resource for better diffs (#101) by @rgibert

## 1.16.2

### Various fixes & improvements

- Prevents --context from falling after the "--". (#167) by @ellisonmarks

## 1.16.1

### Various fixes & improvements

- (fix): add context after cmd (#166) by @mwarkentin

## 1.16.0

### Various fixes & improvements

- Fixes run-pod default deployment for SaaS. (#165) by @ellisonmarks
- feat(debug): Add commandline flag to start debug container priviledged (#164) by @Dav1dde

## 1.15.10

### Various fixes & improvements

- chore: Update default ST deployment (#163) by @rgibert

## 1.15.9

### Various fixes & improvements

- chore(helm): return proper exit codes on apply (#162) by @gi0baro

## 1.15.8

### Various fixes & improvements

- this fixes drift detection with the help of claude code (#161) by @kneeyo1

## 1.15.7

### Various fixes & improvements

- fix(detect-drift): Don't exit when running diff from detect-drift (#160) by @rgibert

## 1.15.6

### Various fixes & improvements

- Adds daemonset, ClusterRole and ClusterRoleBinding to the audit script. (#159) by @ellisonmarks

## 1.15.5

### Various fixes & improvements

- fix(diff): Allow drift detection to force diff to exit 0 (#158) by @rgibert

## 1.15.4

### Various fixes & improvements

- fix: merge arguments passed in Helm functions (#157) by @gi0baro

## 1.15.3

### Various fixes & improvements

- merge config for helm contexts (#156) by @keeakita
- Fixes audit to account for ST getsentry service name. (#155) by @ellisonmarks
- chore: diff should exit non-zero when a diff is present (#150) by @rgibert

## 1.15.2

- No documented changes.

## 1.14.1

### Various fixes & improvements

- remove debug log (#153) by @keeakita
- values_for -> values_of (#153) by @keeakita
- fix loading values with external=True (#153) by @keeakita

## 1.15.0

### Various fixes & improvements

- (fix): add $DEPLOYMENT_IMAGE parsing to statefulset image extension (#152) by @mwarkentin
- chore: remove insecure macros or their insecure variants (#149) by @oioki
- Adds Role, Rolebinding and ScaledObject to audit. (#151) by @ellisonmarks

## 1.14.0

### Various fixes & improvements

- unit tests (#145) by @keeakita
- plumb merge config through (#145) by @keeakita
- helper method, classdoc (#145) by @keeakita
- add merge config scaffolding (#145) by @keeakita

## 1.13.0

### Various fixes & improvements

- fix: set security_hardened=True by default (#148) by @oioki
- fix(debug): Filter duplicated mount paths (#147) by @Dav1dde

## 1.12.0

### Various fixes & improvements

- feat: add helm namespace filter (#146) by @gi0baro

## 1.11.1

### Various fixes & improvements

- fix: Pass back region_name when using alias so lookups always work (#143) by @rgibert
- fix: helm `get_remote_app_version` (#144) by @gi0baro

## 1.11.0

### Various fixes & improvements

- PRODENG-227: handle unknown services (#141) by @mwarkentin
- feat(sentry-kube): add `debug` subcommand (#137) by @oioki

## 1.10.0

### Various fixes & improvements

- PRODENG-227: add sentry-kube get-image cmd (#140) by @mwarkentin
- feat: Allow setting an override image SHA from the CLI for applies (#139) by @rgibert

## 1.9.1

### Various fixes & improvements

- fix(kafkactl): add missing no_security_context=False (#138) by @oioki

## 1.9.0

### Various fixes & improvements

- feat: add helm release strategies (#125) by @gi0baro
- feat(run-pod): add `--no-security-context` option (#136) by @oioki

## 1.8.1

### Various fixes & improvements

- fix(drift): Format code to linear markdown formatting (#135) by @nikhars

## 1.8.0

### Various fixes & improvements

- feat: add `md5` jinja filter (#134) by @oioki

## 1.7.0

### Various fixes & improvements

- PRODENG-159: add service-level feature flag for simplified jinja whitespace handling (#131) by @mwarkentin

## 1.6.0

### Various fixes & improvements

- feat(k8s): security hardening of geoip and pgbouncer sidecar (#133) by @oioki
- chore(pgbouncer): use auth unconditionally (#132) by @oioki

## 1.5.0

### Various fixes & improvements

- add support for multi-level dicts to getval (#129) by @mwarkentin
- fix(config): Aliases aren't used every where (#128) by @rgibert
- (test): fix test_lint failures (#130) by @mwarkentin

## 1.4.0

### Various fixes & improvements

- feat: Support skipping validating regions (#127) by @rgibert
- feat: support region aliases (#124) by @rgibert
- fixed mypy issue (#122) by @ghengiskhanh
- fixed method return call for precommit hook (#122) by @ghengiskhanh
- [pre-commit.ci] auto fixes from pre-commit.com hooks (#122) by @ghengiskhanh
- [pre-commit.ci] auto fixes from pre-commit.com hooks (#122) by @ghengiskhanh
- [pre-commit.ci] auto fixes from pre-commit.com hooks (#122) by @ghengiskhanh
- fixed mypy issues (#122) by @ghengiskhanh
- [pre-commit.ci] auto fixes from pre-commit.com hooks (#122) by @ghengiskhanh
- Customer is now added to the issue; updated globals (#122) by @ghengiskhanh
- added Linear integraiton for Drift tickets and swapped it in instead of jira (#122) by @ghengiskhanh
- [pre-commit.ci] pre-commit autoupdate (#116) by @pre-commit-ci
- improve error messages on missing jinja include (#121) by @keeakita

## 1.3.2

### Various fixes & improvements

- feat(secrets): add `--copy-entry` mode (#119) by @oioki
- feat: universal secrets (#120) by @oioki

## 1.3.1

### Various fixes & improvements

- fix: helm with OCI repos (#118) by @gi0baro

## 1.3.0

- No documented changes.

## 1.2.3

### Various fixes & improvements

- chore(docs): Add note about determining new versions (#117) by @rgibert
- [pre-commit.ci] auto fixes from pre-commit.com hooks (#114) by @victoria-yining-huang
- add combine default values files (#114) by @victoria-yining-huang
- sentry-kube pg: support optional namespace via / (#110) by @oioki
- [pre-commit.ci] pre-commit autoupdate (#113) by @pre-commit-ci
- [pre-commit.ci] pre-commit autoupdate (#94) by @pre-commit-ci
- Revert "chore: Add config defaults" (#111) by @rgibert

## 1.2.2

### Various fixes & improvements

- cleanup: remove unused commands (#108) by @bmckerry

## 1.2.1

### Various fixes & improvements

- chore: Add config defaults (#65) by @rgibert

## 1.2.0

### Various fixes & improvements

- chore: Add support for us region to replace saas (#60) by @rgibert

## 1.1.3

### Various fixes & improvements

- feat(kubectl): remove old private DNS connection to clusters (#109) by @bmckerry

## 1.1.2

### Various fixes & improvements

- fix(readme): update install info (#107) by @bmckerry

## 1.1.1

### Various fixes & improvements

- Minor helm enhancements (#106) by @gi0baro

## 1.1.0

### Various fixes & improvements

- feat(helm): add reversemap support (#105) by @gi0baro

## 1.0.0

### Various fixes & improvements

- Merge pull request #104 from getsentry/gi0baro/helm (#104) by @gi0baro

## 0.0.37

### Various fixes & improvements

- fix: Support .yaml.j2 extensions for templates (#102) by @rgibert

## 0.0.36

### Various fixes & improvements

- ref: use set instead of [] for get_regions (#98) by @rgibert
- feat: Support get-regions filtering by service (#97) by @rgibert

## 0.0.35

### Various fixes & improvements

- change: Add get-var macro (#95) by @dmajere

## 0.0.34

### Various fixes & improvements

- Fix audit script listing CRDs. (#92) by @ellisonmarks
- [pre-commit.ci] pre-commit autoupdate (#89) by @pre-commit-ci
- chore(render_services): Add debug flag for investigating issues (#90) by @rgibert
- clean up ops assistant message (#88) by @mwarkentin
- change: Make docker container buildable (#87) by @dmajere
- [pre-commit.ci] pre-commit autoupdate (#85) by @pre-commit-ci
- Add release instructions (#84) by @mwarkentin

## 0.0.33

### Various fixes & improvements

- Increase default kube api timeout (#83) by @mwarkentin
- feat(render-services): Add an option to render services fast (#82) by @Dav1dde

## 0.0.32

### Various fixes & improvements

- Updates deprecated upload artifact workflow. (#81) by @ellisonmarks
- change: allow kube api calls timeout overrides in ext.py (#80) by @dmajere
- feat(iptables): use latest tag from Artifact Registry (#79) by @oioki
- chore: cleanup `vault` subcommand (#78) by @oioki

## 0.0.31

### Various fixes & improvements

- fix(kube): Ignore empty override files (#76) by @Dav1dde

## 0.0.30

### Various fixes & improvements

- chore: Remove unused EB test code (#75) by @rgibert
- chore: Remove ephemeral bastion code (#67) by @rgibert

## 0.0.29

### Various fixes & improvements

- Removes the region and zone parameters from the ssh command. (#74) by @ellisonmarks

## 0.0.28

### Various fixes & improvements

- detect dns endpoint and skip port forwarding (#72) by @mwarkentin

## 0.0.27

### Various fixes & improvements

- INC-984: allow diff/apply to spawn jobs (#70) by @bmckerry

## 0.0.26

### Various fixes & improvements

- fix(libsentrykube): prevent accidental mapping of group dir name (#68) by @Litarnus

## 0.0.25

### Various fixes & improvements

- feat: Allow additional locals to md5template (#69) by @untitaker

## 0.0.24

### Various fixes & improvements

- fix(libsentrykube): apply name conversion in the entire config tree (#66) by @Litarnus

## 0.0.23

### Various fixes & improvements

- feat(sentrykube): introduce additional possibilities to override config (#63) by @Litarnus

## 0.0.22

### Various fixes & improvements

- chore: Print cluster when using apply/run_job (#64) by @rgibert
- feat(pg): create k8s secrets if they do not exist (#39) by @oioki
- fix: --quiet should mute DD echos (#48) by @rgibert

## 0.0.21

### Various fixes & improvements

- Move out the callback (#58) by @brian-lou

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
