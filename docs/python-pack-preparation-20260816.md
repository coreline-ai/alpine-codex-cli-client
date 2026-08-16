# Production Python pack 준비 기록

기준일: `2026-08-16 KST`

## 결과

Android Runtime이 외부 저장소를 호출하지 않고 APK 내부 파일만으로 Python Gateway를 기동할 수
있도록 production Alpine Python package pack을 준비했다.

| 항목 | 값 |
|---|---|
| Alpine / architecture | `3.21.3` / `aarch64` (`noarch` 의존성 허용) |
| Python | `3.12.14-r0` |
| pack ID | `alpine-3.21.3-python3-3.12.14-r0` |
| package count / bytes | `21` / `17,675,368` |
| lock SHA-256 | `d9be7286cd28a5b6bb2f2a6a378f3a55a01a7d83337856f0a177a7a46790a3b8` |
| SPDX SHA-256 | `f4f7cc90a785191e15b55a1558554d1cc26425b8276856fbeaba1bf508c52ac0` |
| 로컬 입력 | `alpine-python-pack-bundled/src/main/python-pack` (Git-ignored) |

패키지는 Alpine 공식 v3.21 `main/aarch64` 저장소와 signed APKINDEX를 기준으로 고정했다.

- main APKINDEX SHA-256: `20c5300c4a6e9a357321ae3d36426798142c10b052f63c3ed6196560261363cc`
- community APKINDEX SHA-256: `e4df29037482a815967d80d981f56f107afa6fa8d665602cb3a842a146f2dc5f`
- 공식 source: <https://dl-cdn.alpinelinux.org/alpine/v3.21/main/aarch64/>

lock에는 `python3`, 직접·전이 library와 Alpine `install_if`가 선택하는 `pyc`, `python3-pyc`,
`python3-pycache-pyc0`가 모두 포함된다. 각 `.apk`의 name/version/architecture/size/SHA-256은 lock,
license와 download location은 SPDX 2.3 SBOM에 기록되어 있다.

## 발견 및 수정

1. 최초 18-package pack은 정적 dependency closure 검증을 통과했다.
2. 완전 신규 `labdebug` 설치의 `/sbin/apk add --no-network --simulate`에서 Alpine `install_if`가
   pyc split package 3개를 추가로 요구해 preflight가 fail-closed했다.
3. 세 package를 같은 공식 index/version에서 추가해 21-package pack으로 다시 고정했다.
4. Alpine의 architecture-independent package가 정상 pack에서 거부되던 검증기를 수정해
   `aarch64`와 `noarch`만 허용하고 `x86_64` 등 foreign architecture는 계속 거부했다.

## 검증 결과

| 검증 | 결과 |
|---|---|
| source lock/hash/package metadata/SPDX | PASS — 21 packages |
| Gradle production asset 생성·검증 | PASS — 21 packages |
| app release Python pack gate | PASS |
| `debug` APK embedded pack coverage | PASS |
| `secureDebug` APK build·signature·clean-room audit | PASS |
| Samsung `SM-S931N`, API 36 최초 offline 설치 | PASS |
| 기기 Alpine DB의 `python3`/pyc split packages | PASS |
| Python Gateway 및 PRoot process 기동 | PASS — `1/1` |
| TCP `8787` listener | PASS — `0` |
| force-stop 후 Gateway 복구 | PASS — 약 `2.95s` |
| 자동 turn audit 증가 | PASS — `0` |

검증한 `labdebug` APK SHA-256은
`891bb901874b2cf87fc8a359c77a417354ec48effb9b2334b4e48290e2806675`다. 같은 pack을 포함한
`secureDebug` APK SHA-256은
`1d3d0abd4d477170a8fa5c923222083184937718508750fa2a8fba08ef8ca3d6`이며, 기존 실계정 앱 데이터를
불필요하게 변경하지 않도록 빌드와 정적 audit까지만 수행하고 설치하지 않았다.

## 남은 경계

- pack byte는 APK에 포함되므로 설치 후 runtime download가 없다.
- pack source directory 자체는 Git에 포함하지 않는다. 새 build machine에는 동일하게 검토된 로컬
  입력 또는 별도 artifact 보관본이 필요하다.
- 공개 배포용 signed release APK는 외부 release keystore와 예상 certificate SHA-256을 받은 뒤
  별도로 생성·검증해야 한다.
