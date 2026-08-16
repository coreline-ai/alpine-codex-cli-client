# 보안 정책

## 지원 범위

현재는 공개 배포 전 개발 단계이므로 최신 `main`만 보안 수정 대상입니다. 과거 APK hash가 기록된
Phase/Samsung evidence는 재현 기록이며 지원 release가 아닙니다.

## 취약점 보고

보안 문제는 공개 issue에 credential, OAuth URL/code, account 정보, prompt/response, device serial,
raw log 또는 exploit payload를 첨부하지 마세요. 가능하면 GitHub 저장소의 **Private vulnerability
reporting / Security advisory**를 사용하고 다음의 비민감 정보만 먼저 제공하세요.

- 영향받는 commit 또는 version
- Android version/ABI와 build variant
- 재현에 필요한 최소 단계
- 예상 동작과 실제 동작
- credential 없이 만든 synthetic fixture 또는 redacted counter
- 제안하는 완화 방법

실제 OpenAI/xAI 계정 credential이나 CLI-owned authentication 파일은 프로젝트 maintainer에게도
전달하지 않습니다.

## 프로젝트 보안 경계

- Android/Gateway는 Codex/Grok CLI credential을 읽거나 복사하지 않습니다.
- Android↔Gateway 제품 transport는 app-private filesystem UDS, peer UID, HMAC을 요구합니다.
- Provider 직접 API fallback과 API key 입력은 지원하지 않습니다.
- release는 production Python pack과 외부 signing 입력이 없으면 fail-closed합니다.
- Codex/Grok/Gateway/PRoot는 같은 Android UID를 공유하므로 상호 간 커널 sandbox는 아닙니다.
- 루팅/compromised OS, Provider/브라우저 내부, 실제 release key 운영은 별도 보안 범위입니다.

상세 위협 모델과 현재 잔여 위험은 [Security model](docs/security-model.md)과
[보안 검토 및 조치 현황](security_best_practices_report.md)에 있습니다.

## 검증

수정은 실제 credential 없이 다음 gate를 통과해야 합니다.

```bash
sh scripts/verify-secure-debug-milestone.sh
```

실제 OAuth 또는 단말 검증이 필요하면 [Samsung runbook](docs/samsung-grok-secure-debug-runbook.md)의
승인·redaction 경계를 따라야 합니다.
