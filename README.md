# Alpine Codex CLI Client

`Alpine Codex CLI Client`는 Android debug 전용 Codex 채팅 클라이언트입니다. Android 앱은
app-private Alpine 안에서 실행되는 공식 Codex CLI `app-server`에만 loopback Gateway로 연결합니다.

## 현재 상태

Phase 0 프로젝트 골격 단계입니다. OAuth, 모델 목록, 채팅 기능은 아직 구현되지 않았습니다.

## 제품 경계

- Debug application ID: `dev.alpine.codexclient.debug`
- OAuth: 공식 Codex CLI의 Device Code OAuth만 사용
- 채팅: `Android → loopback Gateway → codex app-server`만 사용
- 미지원: API key, 앱 소유 OAuth client ID, CLI fingerprint, Provider 직접 HTTPS fallback
- 배포: Play Store·release signing·release artifact는 이 프로젝트 범위에 포함하지 않음

## 개발 시작

JDK 17과 Android SDK 36이 필요합니다.

```bash
./gradlew :app:assembleDebug
```

상세 구현 순서와 검증 기준은 [개발 계획](dev-plan/implement_20260811_133123.md)을 따른다.
