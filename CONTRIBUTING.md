# 기여 가이드

Alpine Agent CLI Client에 기여할 때는 기능 확장보다 credential 비노출, closed protocol,
fail-closed 배포 경계를 우선합니다.

## 개발 환경

- JDK 17
- Android SDK 36
- Python 3
- `arm64-v8a` Android 기기 또는 credential-free JVM/fixture 환경

SDK와 JDK 경로는 개인 환경에서 설정하고 `local.properties`, keystore, APK/AAB, OAuth 상태,
device capture를 커밋하지 않습니다.

## 변경 원칙

1. Android/Gateway가 API key, bearer token, CLI credential 파일을 읽는 경로를 추가하지 않습니다.
2. Provider 직접 HTTPS fallback, prompt 자동 retry/replay, cross-Agent fallback을 추가하지 않습니다.
3. executable, argument, environment, app-server/ACP method를 Android 입력으로 선택하게 하지 않습니다.
4. Runtime package 또는 dependency를 변경하면 lock, SBOM, notice와 verifier를 함께 갱신합니다.
5. 실제 OAuth, account, model, prompt, response, URL/code와 raw log를 fixture/evidence에 넣지 않습니다.
6. `release` gate를 우회하거나 test/debug 구현을 production source에 fallback으로 연결하지 않습니다.

## 검증

문서만 변경한 경우에도 Markdown local link와 `git diff --check`를 확인합니다. 코드, build script,
lock 또는 정책을 변경한 경우 전체 credential-free gate를 실행합니다.

```bash
sh scripts/verify-secure-debug-milestone.sh
```

실제 계정 검증은 credential-free gate 통과 후 Samsung runbook의 별도 승인 지점에서만 수행합니다.
logout, clear-data, uninstall, Runtime shutdown, paid turn과 release signing은 각각 해당 작업의 명시적
승인 없이 실행하지 않습니다.

## Dependency와 artifact 변경

- 동적 version, range, `latest.*`, `SNAPSHOT`은 허용하지 않습니다.
- lockfile 갱신은 명시적 dependency 변경과 함께 별도 diff로 검토합니다.
- Codex/Grok binary upgrade는 upstream source, artifact size/hash, ELF, license/notice와 protocol/profile
  회귀를 다시 검토합니다.
- production Python pack과 release signing material은 Git 밖에서 제공하고 저장소가 다운로드하거나
  생성하지 않게 유지합니다.

## 문서

제품 동작이나 보안 경계를 변경하면 다음을 함께 검토합니다.

- `README.md`
- `docs/project-overview.md`
- `docs/architecture.md`
- `docs/security-model.md`
- `docs/public-release.md`
- 관련 공급망/notice/evidence 문서

과거 APK hash와 실기기 evidence는 덮어쓰지 말고 새 artifact identity와 날짜를 별도 기록합니다.

## 라이선스

기여한 프로젝트 코드는 저장소의 [GPL-3.0](LICENSE)에 따라 배포되는 것에 동의해야 합니다.
제3자 코드나 asset을 추가할 때는 원본 라이선스, notice, source revision과 적용 범위를 명확히
기록해야 합니다.
