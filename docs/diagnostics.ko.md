# 컴포넌트 개발자를 위한 공통 진단 구현 지침

[English](diagnostics.md) | **한국어**

blesession을 사용하는 Home Assistant 통합에서 같은 의미의 진단 정보를 수집하고 표시하기 위한 구현 기준입니다. 새 컴포넌트를 만들거나 기존 진단을 정리할 때 이 문서를 적용합니다. 센서와 진단 다운로드는 각 통합에서 구현하며, blesession이 자동 생성하지 않습니다.

이 문서의 **필수**는 이 공통 구성에 맞추기 위한 규칙, **권장**은 기기 특성에 따라 조정할 수 있는 기본안입니다. 필드·API의 정확한 정의는 [계약](contract.md), 프로토콜 구현은 [도입 가이드](adopting.md)를 따릅니다. 예제는 현재 소스의 API를 기준으로 하므로 설치된 라이브러리가 해당 기능을 제공하는지 확인하세요. 의존성 선언은 `"requirements": ["blesession"]`로 작성하며 이 지침에서는 버전을 고정하지 않습니다.

## 1. 공통 구성과 책임

```text
BLE 작업 → SessionTrace / radio_facts → 보고서 생성 → SessionReports.record
                                                       ├─ HA 센서 갱신
                                                       └─ 진단 다운로드
```

| 영역 | blesession | 컴포넌트에서 구현할 것 |
|---|---|---|
| 세션 | 단계·시간·연결 상태 기록 | 실제 작업에 같은 trace 전달, 기기별 단계 매핑 |
| 보고서 | 결과·공통 실패 원인·필드 구성 | 작업 이름, 기기별 원인 설명, 고유한 추가 정보 |
| 보존 | 최근 시도·실패·재시도, 횟수와 시각 | 기기별 저장 범위, HA 갱신, 명시적 초기화 정책 |
| 표시 | 보고서 dict와 통계 제공 | 센서, 번역, 진단 다운로드, 개인정보 제거 |

**필수:** 하나의 BLE 기기 런타임 객체에 하나의 `SessionReports`를 유지합니다. 여러 기기를 관리하는 통합은 기기별로 분리합니다. 같은 기기의 `print`, `poll`, `measure` 등은 안정적인 `operation` 이름으로 구분하며, 작업별 최근 결과는 `reports.of(operation)`을 사용합니다. 임의로 복사한 `SessionReports`에 기록하지 않습니다.

## 2. 모든 시도를 한 번씩 기록

**필수:** `run_attempts()`의 `on_attempt`에서 보고서를 기록합니다. 반환된 마지막 시도를 다시 기록하면 횟수가 중복되고, 마지막 시도만 기록하면 중간 실패 정보가 사라집니다.

다음 함수는 컴포넌트의 보고서 어댑터 예제입니다. `cause`는 `Failure` 한 개를 받는 기기별 설명 함수이며 필요 없으면 생략합니다. 보고서 생성에 예외가 생겨도 최소 보고서를 남깁니다. 경고를 예외로 처리하도록 설정한 개발 환경의 `Warning`은 그대로 전파합니다.

```python
from blesession import fallback_report, report_attempt
from blesession.hass import radio_facts


def make_recorder(reports, hass, address, operation, max_attempts, *, cause=None):
    def record(attempt):
        try:
            report = report_attempt(
                attempt,
                operation=operation,
                attempts=max_attempts,
                facts=radio_facts(hass, address, attempt.trace.link),
                cause=cause,
            )
        except Warning:
            raise
        except Exception:
            report = fallback_report(operation, attempt=attempt)
        reports.record(report)

    return record
```

기존 `run_attempts()` 호출에 `on_attempt=make_recorder(reports, hass, address, "write", max_attempts)`를 연결합니다. `attempt_timeout_s`를 지정하고 기기 핸들은 시도 안에서 새로 구합니다. `guard` 결과는 JSON으로 저장할 수 있는 문자열 등으로 유지합니다. 어댑터가 fallback을 사용했다면 컴포넌트의 기존 debug logger에도 원인을 남기세요.

`run_attempts()`를 사용하지 않는 통합은 `build_report()`와 `reports.record()`를 사용하되 같은 정책을 지킵니다. 외부 취소(`CancelledError`)는 실패로 기록하지 않고 전파합니다. 작업 제한 시간 만료로 반환된 `AttemptTimedOut`은 보고 대상입니다. 잠든 기기에 대한 정상적인 폴링 실패를 제외할지는 통합에서 정하고 사용자 안내에도 명시합니다.

| 결과 | 저장·통계 규칙 |
|---|---|
| 성공 | `last`, 작업별 최근 결과, `sessions`, `last_success_at` 갱신; 연속 실패 초기화 |
| 최종 실패 | `last`, `last_failure`, 작업별 최근 결과 갱신; `failures`, `sessions`, `consecutive_failures` 증가 |
| 다음 시도가 있는 실패 | `last`, `last_retry`, 작업별 최근 결과 갱신; 최종 실패 횟수에 포함하지 않음 |
| 실행 거절(`skipped`) | `last`, 작업별 최근 결과만 갱신; 성공·실패 횟수에 포함하지 않음 |

성공이 과거의 `last_failure`나 `last_retry`를 지우지 않게 합니다. 통계는 기본적으로 런타임 수명 동안 누적됩니다. 영속 복구를 추가한다면 범위를 문서화하고, 재연결이나 성공할 때마다 `clear()`를 호출하지 마세요. 실패율은 `sessions > 0`일 때만 `failures / sessions`로 계산합니다.

## 3. 권장 센서 구성

다음은 공통 역할입니다. 표시 이름·번역·기존 entity ID는 각 통합의 체계에 맞추고, 진단 통일을 위해 기존 unique ID를 바꾸지 않습니다. 진단용 엔티티에는 `EntityCategory.DIAGNOSTIC`을 사용합니다.

| 역할 | 상태 | 속성 |
|---|---|---|
| 최근 작업 / 작업 소요시간 | 실제로 측정한 기기별 값 | 해당 작업의 `reports.of(operation)` 전체 |
| 마지막 실패 | `reports.last_failure_at` | `reports.last_failure` 전체 |
| 실패 횟수 | `reports.failures` | 필요하면 전체 완료 횟수·연속 실패 요약 |
| 선택: 마지막 성공 | `reports.last_success_at` | 필요하면 성공 이력 설명 |

**필수:** 보고서 필드를 허용 목록으로 골라 복제하지 말고, 개인정보를 제거한 **보고서 전체**를 속성으로 노출합니다. 새 공통 필드도 그대로 전달되게 하며 필드 이름·단위·의미를 재정의하지 않습니다. 원본 보고서 dict는 수정하지 않습니다.

마지막 실패 센서는 UTC timestamp를 사용하고 기록 전에는 `None`입니다. 소요시간 센서는 기기에서 측정한 작업 전체 시간과 개별 `<stage>_s`를 구분합니다. 중첩 단계와 락 대기 시간을 무조건 합산해 작업 시간을 만들지 마세요. 작업 중에는 이전 작업 보고서를 현재 진행 시간과 섞지 않도록 숨기거나 이전 결과임을 명시합니다.

ICScale처럼 마지막 실패만 표시하는 기존 통합은 성공 보고서를 진단 다운로드로 제공할 수 있습니다. 새 센서를 만드는 경우 기존 센서 정책과 기본 활성화 여부를 검토합니다. 모든 보고서를 모든 센서에 중복으로 붙일 필요는 없습니다.

## 4. 보고서 기록과 HA 갱신 연결

보고서를 저장하는 것만으로 HA의 센서 속성이 갱신되지는 않습니다. 상태 값이 이전과 같아도 새로운 보고서와 실패 정보가 화면에 반영돼야 합니다.

**필수:** `SessionReports.add_listener()`를 기존 coordinator의 갱신 경로 또는 엔티티의 상태 쓰기에 연결합니다. 같은 엔티티에 대해 두 경로를 중복 등록하지 말고, 엔티티 제거·통합 unload 때 반환된 해제 함수를 호출합니다. 콜백은 추가 BLE 작업이나 네트워크 조회를 수행하지 않습니다.

아래는 기존 센서 클래스에 적용할 수명 주기 예제입니다. 센서에 `self._reports`가 설정되어 있고 `extra_state_attributes`가 해당 보고서의 비식별 복사본을 반환한다고 가정합니다. coordinator를 사용하는 엔티티는 같은 알림을 기존 coordinator 경로로 연결해도 됩니다.

```python
async def async_added_to_hass(self):
    await super().async_added_to_hass()
    self.async_on_remove(
        self._reports.add_listener(self.async_write_ha_state)
    )
```

이 예제는 HA 이벤트 루프 안에서 기록하는 경우입니다. 다른 스레드에서 직접 `async_write_ha_state()`를 호출하지 않습니다. `clear()` 후에도 화면을 즉시 갱신하려면 컴포넌트에서 별도로 갱신을 알립니다. `clear()`는 기록용 listener를 호출하지 않습니다. HA의 [엔티티 수명 주기](https://contributors.home-assistant.io/core/entities/lifecycle/)를 함께 참고하세요.

## 5. 최근 추가된 정보도 빠짐없이 전달

아래 항목은 별도의 진단 센서를 각각 만들기보다 보고서 속성으로 유지합니다. 전체 정의와 원인 키는 [보고서 계약](contract.md)을 기준으로 합니다.

| 정보 | 수집 경로·해석 |
|---|---|
| `lock_wait_s` | `run_attempts(lock=...)`가 기록; BLE 전송 시간·실패 단계로 해석하지 않음 |
| `link_dropped`, `dropped_after_s` | `ble_session()`이 관찰한 자발적 끊김과 연결 수명; 재사용 링크는 현재 작업 시간보다 길 수 있음 |
| `mtu` | `ble_session()`이 종료 전 읽은 실제 보고값; 모르면 생략 |
| `last_seen_s`, `paths_passive` | `radio_facts()`의 광고 수신 경과 시간·연결 불가능한 수신기 수 |
| `slots_free`, `slots` | `via`로 표시된 수신기의 조회 시점 슬롯 상태; 과거 실패 당시 상태라고 단정하지 않음 |
| `error_before_timeout`, `timed_out_in` | timeout 전 원래 오류와 취소가 실제 발생한 단계; `error`, `failed_stage`와 함께 보존 |
| `via_unconfirmed` | 표시한 수신기가 실제 연결 경로로 확인됐는지 판단할 때 함께 사용 |
| `sessions`, `consecutive_failures`, `last_success_at` | `SessionReports`의 요약 통계; 개별 세션 보고서의 자동 필드는 아님 |

**필수:** 관찰하지 못한 값은 생략하거나 `None`으로 두며 임의의 `0`, `False`, 성공으로 채우지 않습니다. 새 필드가 없는 이전 기록도 읽을 수 있어야 합니다. `likely_cause`는 추정이며 확정 진단으로 표시하지 않습니다. 예를 들어 `paths`는 빈 슬롯 수가 아닙니다.

`NotificationTimeout.rejected`는 수신했지만 채택하지 않은 프레임 수입니다. `SessionDropped.sent/total`, `WriteTimeout.sent/total`은 중단된 `write_chunks()`의 완료 쓰기 수·전체 쓰기 수이며 bytes가 아닙니다. 이 예외 속성이 모두 최상위 보고서 키로 자동 복사된다고 가정하지 마세요. 구조화해 추가하려면 `transfer_writes_sent`처럼 충돌하지 않는 고유 이름을 사용합니다.

연결을 직접 구현한 통합은 보고서를 그대로 노출해도 `ble_session()`의 끊김·MTU 수집이 저절로 생기지 않습니다. 지원 가능한 수집 경로를 연결하거나 해당 정보가 없는 상태를 유지하세요. 수집하지 않은 정보를 추정으로 만들어 넣지 않습니다.

## 6. 진단 다운로드의 공통 형태

**권장:** `diagnostics.py`를 제공하고 아래의 `blesession` 구획을 유지합니다. 센서 속성 노출과 진단 다운로드는 별도 기능입니다. 다운로드는 저장된 기록을 읽으며 새 연결·폴링·스캔을 시작하지 않습니다. 여러 기기는 각 기기 구획 아래에 이 형태를 반복합니다.

다음 helper는 개인정보 제거 **전의 내부 복사본**을 만듭니다. `None`은 아직 기록이 없다는 뜻입니다. 새 보고서 필드를 유지하되 요약 통계는 일관된 위치에 둡니다.

```python
from copy import deepcopy


def diagnostics_snapshot(reports):
    def timestamp(value):
        return value.isoformat() if value is not None else None

    return {
        "blesession": {
            "summary": {
                "last_kind": reports.last_kind,
                "sessions": reports.sessions,
                "failures": reports.failures,
                "consecutive_failures": reports.consecutive_failures,
                "last_success_at": timestamp(reports.last_success_at),
                "last_failure_at": timestamp(reports.last_failure_at),
            },
            "last": deepcopy(reports.last),
            "last_failure": deepcopy(reports.last_failure),
            "last_retry": deepcopy(reports.last_retry),
            "by_operation": deepcopy(reports.by_operation_last),
        }
    }
```

통합의 `async_get_config_entry_diagnostics(hass, entry)`에서 runtime data의 대상 `reports`를 구한 뒤, 이 복사본에 다음 절의 비식별화를 적용해 반환합니다. 위 helper 자체는 HA의 진단 진입점이 아닙니다. Python 객체·예외·원본 bytes를 포함하지 말고, 최종 결과가 JSON으로 직렬화되는지 테스트합니다. 진입점과 테스트 구성은 HA의 [진단 구현 안내](https://contributors.home-assistant.io/core/integrations/diagnostics/)를 따릅니다.

## 7. 개인정보 제거와 기기별 확장

**필수:** 센서 속성이나 다운로드에 내보내기 전에 복사본을 비식별화합니다. 컴포넌트가 공통으로 사용하는 비식별 함수를 두고 다음을 검사합니다.

- 키·토큰·비밀번호·본딩 정보, 원본 설정 전체를 내보내지 않습니다.
- Bluetooth/MAC 주소·장치 식별자·내부 IP/호스트명·개인 이름·측정값은 제거하거나 일관된 익명 값으로 치환합니다. `via`, `advertised_via`도 식별 정보를 포함할 수 있습니다.
- 중첩 dict/list와 `error`, `error_before_timeout`, `likely_cause` 같은 문자열 내부도 처리합니다. HA의 `async_redact_data`로 특정 키를 가리는 것만으로 문자열 속 주소까지 제거되지는 않습니다.
- 비교에 필요한 작업·단계·시간·횟수는 보존하고 원본 `SessionReports`는 변경하지 않습니다. 새 필드가 추가될 때도 개인정보 검토를 수행합니다.

고유 정보는 `trace.note(payload_bytes=..., protocol_retries=...)` 같은 scalar 값으로 추가합니다. `REPORT_KEYS`는 예약되어 있어 `note(error=...)`, `note(skipped=...)`로 바꾸려 하면 무시됩니다. 예약 목록 밖의 공통 필드도 덮어쓰지 말고 기기·프로토콜별 이름을 사용하세요. 큰 패킷·이미지·측정 이력은 보고서에 넣지 않습니다. `cause(failure)`는 기기 고유 설명이 없으면 `None`을 반환해 공통 원인 설명을 유지합니다.

번역은 `likely_cause_key`를 활용하되 모르는 새 키에서도 원래 설명으로 fallback합니다. 원본 필드나 키를 번역 문자열로 바꾸지 마세요. 라이브러리 업데이트로 새 필드가 전달되는 것과 새 센서·번역·진단 다운로드가 자동 생성되는 것은 별개입니다.

## 8. 적용 완료 확인

- [ ] 같은 기기의 모든 작업이 같은 `SessionReports`에 한 번씩 기록된다.
- [ ] 실패 후 성공해도 마지막 실패가 남고, 재시도 후 성공은 최종 실패 횟수를 늘리지 않는다.
- [ ] 실행 거절과 외부 취소를 실제 작업 실패와 구분한다.
- [ ] 센서 상태 값이 같아도 새 속성이 갱신되며, unload 후 listener가 남지 않는다.
- [ ] 임의의 새 안전한 보고서 필드가 센서·다운로드에 그대로 전달된다.
- [ ] 기기별 최근 결과·새 진단 필드·요약 통계를 같은 의미로 노출한다.
- [ ] 보고서가 없는 초기 상태, 필드가 부족한 과거 기록, 지원되지 않는 수집 경로를 처리한다.
- [ ] 다운로드가 BLE 작업을 실행하지 않고 최종 결과는 JSON으로 직렬화된다.
- [ ] 중첩 문자열 속 주소·인증 정보가 제거되며 비식별화로 원본 보고서가 바뀌지 않는다.
- [ ] 기기 문서에 진단 센서 위치, 통계 초기화 범위, 다운로드 방법을 안내한다.

기존 통합은 수집 경로, 기록 지점, 센서 갱신, 다운로드 순서로 적용합니다. 기기 프로토콜·재시도 정책·기존 entity ID를 이 지침에 맞추기 위해 함께 바꿀 필요는 없습니다.
