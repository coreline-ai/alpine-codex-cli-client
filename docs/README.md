# 문서 인덱스

기준일: `2026-08-16 KST`

이 디렉터리는 현재 제품 문서와 구현 과정의 역사적 evidence를 함께 보존한다. 처음 읽는 경우
아래 순서를 권장한다.

## 시작 문서

| 문서 | 내용 |
|---|---|
| [`../README.md`](../README.md) | 기능, 요구 환경, 빌드, variant, 현재 제약 |
| [`project-overview.md`](project-overview.md) | 제품 목적, 전체 모듈, 실행·데이터 흐름, 현재 상태 |
| [`architecture.md`](architecture.md) | Runtime topology, 저장소와 lifecycle invariant |
| [`security-model.md`](security-model.md) | 위협 모델, 신뢰 경계, 보안 통제와 잔여 위험 |
| [`../security_best_practices_report.md`](../security_best_practices_report.md) | 전문가 검토 발견 사항별 조치 현황 |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | 보안·검증·artifact 변경을 포함한 기여 규칙 |
| [`../SECURITY.md`](../SECURITY.md) | 지원 범위, 비공개 취약점 보고와 민감정보 금지 규칙 |

## 배포와 공급망

| 문서 | 내용 |
|---|---|
| [`public-release.md`](public-release.md) | production Python pack, 외부 서명, APK/AAB 최종 검증 |
| [`python-pack-preparation-20260816.md`](python-pack-preparation-20260816.md) | 현재 production Python pack provenance와 Samsung 최초 설치 evidence |
| [`runtime-supply-chain.md`](runtime-supply-chain.md) | Alpine/PRoot/Python lock, SPDX, rollback |
| [`gradle-supply-chain.md`](gradle-supply-chain.md) | dependency lock, repository/cache 정책과 미완료 checksum |
| [`debug-sbom.md`](debug-sbom.md) | variant 공통 component inventory와 SBOM |
| [`codex-cli-notice.md`](codex-cli-notice.md) | Codex CLI artifact lock와 upstream notice |
| [`grok-cli-notice.md`](grok-cli-notice.md) | Grok CLI artifact lock와 upstream notice |
| [`reference-source-map.md`](reference-source-map.md) | 검토한 기준 저장소/source revision 추적 |
| [`reference-runtime-adaptations.md`](reference-runtime-adaptations.md) | 기준 Runtime에서 의도적으로 변경한 파일 |

## Agent 계약

| 문서 | 내용 |
|---|---|
| [`grok-gateway-contract.md`](grok-gateway-contract.md) | Android에 노출되는 Agent/Grok normalized route |
| [`grok-runtime-policy.md`](grok-runtime-policy.md) | Grok executable, profile, ACP method와 process 정책 |
| [`grok-preflight.md`](grok-preflight.md) | 실제 OAuth 전 credential-free 사전 검증 기록 |

## 데이터 보호와 회귀

| 문서 | 내용 |
|---|---|
| [`backup-migration.md`](backup-migration.md) | cloud/D2D 제외와 versioned sensitive-state migration |
| [`security-regression-matrix.md`](security-regression-matrix.md) | 기존 기능을 고정하는 자동/실기기 회귀 계약 |
| [`app-real-use-qa.md`](app-real-use-qa.md) | module 검증과 분리된 실제 앱 full QA 전략·harness·판정 기준 |

## Samsung 실기기 검증

아래 문서는 민감 값을 제거한 시점별 evidence다. APK hash, version과 테스트 수는 해당 시점의
artifact에만 적용된다.

| 문서 | 성격 |
|---|---|
| [`samsung-grok-secure-debug-runbook.md`](samsung-grok-secure-debug-runbook.md) | 실제 계정 검증 절차와 금지 동작 |
| [`samsung-app-real-use-qa-20260816.md`](samsung-app-real-use-qa-20260816.md) | 신규 독립 QA Agent와 반복 harness의 비파괴 앱 lifecycle evidence |
| [`samsung-grok-secure-debug-e2e.md`](samsung-grok-secure-debug-e2e.md) | Grok OAuth/turn/Stop/복구 evidence |
| [`samsung-backup-migration-evidence.md`](samsung-backup-migration-evidence.md) | 데이터 보존 update와 no-backup migration evidence |
| [`samsung-debug-e2e-evidence.md`](samsung-debug-e2e-evidence.md) | 초기 및 후속 debug E2E 기록 |
| [`grok-phase8-security-evidence.md`](grok-phase8-security-evidence.md) | 실제 인증 전 보안 gate의 역사적 기록 |
| [`grok-phase9-handoff.md`](grok-phase9-handoff.md) | Grok 구현 완료 시점의 인계 기록 |

## 비교와 역사적 기록

- [`anyclaw-analysis.md`](anyclaw-analysis.md): AnyClaw의 Android Codex OAuth 구현과 보안 경계 비교
- `grok-phase*`, `grok-preflight.md`, `dev-plan/`: 단계별 개발 당시의 결정과 수치

Phase 문서에 남아 있는 `loopback`, 과거 package version, APK hash와 테스트 수는 당시 재현성을
위한 기록이다. 최신 제품 경로는 private UDS이며, 최신 전체 상태는
[`project-overview.md`](project-overview.md)를 기준으로 한다.
