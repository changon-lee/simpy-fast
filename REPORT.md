# SimPy 4.0.2 성능 개선 보고서

## 결론

SimPy 내부를 고치고 핵심 모듈을 Cython으로 컴파일해서, 원본 SimPy 4.0.2(Python 3.9) 대비 다음과 같이 빨라졌습니다. API와 시뮬레이션 결과는 원본과 같습니다.

| 실행 환경 | 9개 워크로드 합계 | dse_mix (복합 모델) | 가장 느린 워크로드 |
|---|---|---|---|
| **개선본 + Python 3.9** (현재 환경) | **2.06배** | 1.99배 | 1.57배 (conditions) |
| **개선본 + Python 3.12** | **2.16배** | 2.05배 | 1.72배 (timeouts) |
| **개선본 + Python 3.14** | **2.37배** | 2.22배 | 2.01배 (conditions) |
| 참고: 원본 + Python 3.12 | 1.45배 | 1.39배 | |
| 참고: 원본 + Python 3.14 | 1.57배 | 1.45배 | |

- 모든 배수는 원본 SimPy를 Python 3.9에서 돌린 결과(합계 9.90초) 대비입니다.
- 측정 방식: 조합마다 새 프로세스에서 5라운드를 번갈아 실행했고, 위 숫자는 중앙값입니다. 원본 파일은 `bench/results/final.txt`입니다.
- 결과 동일성: 6개 조합 모두 9개 워크로드의 이벤트 순서 trace digest가 원본과 똑같습니다(불일치 0건).
- 라이브러리 연산 자체(사용자 코드를 거의 뺀 측정)는 Python 3.9에서 **2.5~3.6배** 빨라졌습니다(아래 연산별 표 참고).
- 사용자분 모델에서 실제로 얼마나 빨라질지는 전체 시간 중 SimPy가 차지하는 비중에 따라 달라집니다. 사용자 코드(generator 본문, random 호출 등)는 이번 작업으로 빨라지지 않습니다.

## 어떻게 했는가

제안하신 6단계를 그대로 따랐습니다. 다만 변경은 "하나 바꾸고 → 측정하고 → 유지 또는 되돌리기"를 반복하는 방식으로 진행했습니다.

1. **기준 성능 측정.** 결정적(seed 고정) 워크로드 9개로 하네스를 만들었습니다(`bench/workloads.py`). 각 워크로드는 시뮬레이션 중 주요 시점을 trace로 남기고, 그 해시(digest)로 원본과 개선본의 이벤트 순서가 같은지 확인합니다. 같은 코드끼리 돌린 A/A 측정의 노이즈는 순수 Python에서 ±2%, 컴파일 트리끼리는 ±4%였습니다.

   | 워크로드 | 내용 |
   |---|---|
   | timeouts | 프로세스 500개 × timeout 400회 |
   | spawn | 짧은 프로세스 10만 개 생성, 일부 프로세스를 yield로 대기 |
   | resource | `Resource(4)`에 사용자 1000명이 경쟁(큐가 김) |
   | priority_resource | `PriorityResource(4)`에 사용자 400명, 무작위 priority |
   | container | producer/consumer 각 200개가 `Container` 공유 |
   | store | 용량 10인 `Store` 6단 파이프라인, 아이템 3만 개 |
   | prio_filter_store | `PriorityStore`와 `FilterStore` |
   | conditions | `a \| b`, `all_of` 조건 이벤트 |
   | dse_mix | 생산 라인 모델: 짧은 job 프로세스 1.2만 개, 스테이션 6개(Resource/PriorityResource), 버퍼 Store, 공유 Container와 보충 프로세스, `req \| timeout` renege |

2. **코드 전체 확인.** `core.py`, `events.py`, `resources/*.py`를 포함해 약 2,100줄을 전부 읽었습니다.
3. **최적화 지점 도출과 프로파일링.** 원본은 cProfile로 봤습니다. 컴파일 버전은 내부가 보이도록 `CY_PROFILE=1` 빌드를 만들어 봤습니다. 연산별 마이크로벤치(`bench/micro.py`)로는 어떤 연산이 덜 빨라졌는지 찾았습니다.
4. **개선 적용.** 프로파일에서 비중이 큰 것부터 적용하고, 매번 측정해서 노이즈보다 큰 개선만 남겼습니다.
5. **회귀 게이트.** 모든 변경은 `bench/gate.sh`를 통과해야 유지했습니다.
   - upstream 테스트 스위트를 돌립니다.
   - API 경계 동작 probe 29개(`bench/compat.py`)를 돌립니다. float/numpy priority, inf capacity, `fail()`에 예외가 아닌 값 등을 넣어 보고, 출력이 원본과 **글자 단위로 같아야** 통과합니다.
   - 벤치마크 digest가 원본과 같아야 합니다.
6. **개선 후 측정.** Python 3.9, 3.12, 3.14 각각에서 원본과 개선본을 한꺼번에 번갈아 측정했습니다.

## 무엇을 했는가 (적용한 변경)

괄호 안 수치는 해당 변경 직전 상태와 비교한 측정값입니다.

| # | 변경 | 효과 |
|---|---|---|
| H1 | `SortedQueue.append`: 요청마다 큐 전체를 다시 정렬(O(n log n), lambda를 n번 호출)하던 것을 이진 삽입(bisect_right)으로 바꿈. 순서는 기존과 같음 | priority_resource **2.99배** |
| H2 | `Environment.run()`: 이벤트마다 `step()`을 호출하던 것을 루프 안으로 인라인. `step`을 재정의하거나 패치한 경우(RealtimeEnvironment, 모니터링 패치)는 기존 경로 유지 | 3~8% |
| H3/H4 | 이벤트 스케줄링을 `heappush`로 직접 인라인하고 `Process._resume`을 정리 | timeouts·spawn 약 1.17배 |
| H5 | `Put`/`Get`: `super().__init__` 체인 제거, `_trigger_put/_get` 루프를 지역 변수로 바인딩 | store·dse_mix 1.10배 |
| C | 핵심 6개 모듈(core, events, resources/base, container, resource, store)을 Cython으로 컴파일. `.py`는 그대로 두고 `.pxd`로 타입을 붙이는 pure-Python 모드 | 단독 약 1.75배 |
| H6 | `Environment`, `Event`, `Timeout`, `Initialize`, `Process`, `Condition`을 cdef 클래스(C 구조체 필드)로 전환 | 1.15배 |
| H7 | hot path 지역 변수에 타입 지정, `__cinit__` 인자 제거 | 1.08배 |
| H10 | `Put`/`Get` 계열 이벤트(Request, Release, StorePut, ContainerGet 등)를 cdef로 전환 | resource 1.14배, dse_mix 1.09배 |
| H11 | 리소스 클래스(BaseResource, Resource, Store, Container 등)를 cdef로 전환하고 `_do_*`/`_trigger_*`를 cpdef(C 디스패치)로 바꿈. Python 서브클래스의 재정의는 그대로 존중 | dse_mix 1.07배 |
| H12 | `Event.succeed`를 cpdef로 바꾸고, 아래의 정확성 수정 | 1.03배 |
| P | 설치용 `setup.py`(wheel 빌드)와 `importlib.metadata` 기반 버전 조회. setuptools 81 이상에서 원본 4.0.2가 import부터 실패하는 문제를 해결 | |

시도했지만 노이즈 범위라서 **되돌린 것**:
- Condition 초기화 정리 (0.97~1.03배)
- Cython `binding=False` (0.98배)
- 힙 항목을 `(time, priority·2^62+eid, event)` 3-tuple로 압축 (0.99배)
- 리소스 필드만 cdef로 바꾸기 (1.01배). 이후 H11로 합쳐서 다시 적용

## 정확성 검증과 발견한 버그

- **upstream 테스트 스위트**: 원본과 같은 결과입니다. 129개 통과, 1개 실패(`test_exception_chaining`)인데, 이 실패는 원본도 Windows에서 경로 정규식 문제로 똑같이 실패합니다. 벽시계 시간에 의존하는 `test_rt.py`는 원본에서도 흔들려서 최대 3회 재시도합니다. `test_rt_slow_sim_default_behavior`는 Python 3.12/3.14에서 원본도 항상 실패하므로 제외했습니다.
- **trace digest**: 3개 Python 버전의 원본과 개선본 6개 조합 모두에서 일치합니다.
- **작업 중 발견하고 고친 버그**: Cython은 기본적으로 함수 인자의 타입 annotation을 실제 타입 검사로 씁니다. 그래서 컴파일 버전이 원본과 다른 결과를 냈습니다.
  - `PriorityRequest(priority=1.5)`의 priority가 1로 잘려서 **요청 처리 순서가 바뀌었습니다.**
  - `Resource(capacity=inf)`가 OverflowError를 냈습니다.
  - `fail('x')`의 예외 종류가 바뀌었습니다.

  벤치는 정수 priority만 썼고 기존 테스트도 이 경우를 다루지 않아서 처음에는 드러나지 않았습니다. `annotation_typing=False`로 annotation을 문서 용도로만 남기고, 모든 타입 지정을 `.pxd`로 옮겨서 고쳤습니다. 같은 문제가 다시 생기지 않도록 `bench/compat.py`를 게이트에 넣었습니다.

## 원본과 동작이 다른 점 (알고 쓰셔야 할 것)

일반적인 사용(`env.process`, `timeout`, `Resource`/`Store`/`Container`의 `request`·`put`·`get`, `with` 문, interrupt, 조건 이벤트)에는 차이가 없습니다. 차이는 다음과 같은 드문 사용 방식에서만 생깁니다.

1. `Environment.schedule()`을 서브클래스에서 재정의해도 내장 이벤트(succeed, timeout 등)는 그 메서드를 거치지 않고 큐에 직접 넣습니다. `env.step`을 재정의하거나 패치하는 방식은 그대로 동작합니다.
2. (컴파일 버전) 이벤트의 `env`는 `Environment`(또는 그 서브클래스)이거나 None이어야 합니다. `Put`/`Get`의 `resource`는 `BaseResource` 서브클래스여야 합니다. duck-typing으로 만든 가짜 env나 가짜 리소스는 TypeError가 납니다.
3. (컴파일 버전) `Put`, `Get`, `BaseResource`가 더 이상 `typing.Generic`/`ContextManager`를 상속하지 않습니다. `Put[X]` 같은 subscript와 `isinstance(req, AbstractContextManager)`는 그대로 동작합니다.
4. (컴파일 버전) `Resource`/`Store`/`Container`의 `_do_put`/`_do_get`은 자기 이벤트 타입(Request, StorePut 등)을 기대합니다. 이 메서드를 Python 서브클래스에서 재정의하는 것은 그대로 지원합니다.
5. `run()` 경로의 traceback에 `step` 프레임이 하나 빠집니다.

## 사용 방법

```bat
:: 현재 Python 3.9 환경에 설치 (기존 simpy 4.0.2를 대체)
pip install C:\Users\82103\simpy\dist\simpy-4.0.2+fast1-cp39-cp39-win_amd64.whl
:: 되돌리기
pip install simpy==4.0.2
```

- 코드는 수정할 필요가 없습니다. `import simpy` 그대로 씁니다.
- `simpy.__version__`이 `4.0.2+fast1`이고 `simpy.events.__file__`이 `.pyd`로 끝나면 컴파일 버전이 로드된 것입니다.
- Python 3.12/3.14용 wheel도 `dist/`에 있습니다(`cp312`, `cp314`). 세 wheel 모두 깨끗한 venv에 설치해서 테스트했습니다.
- 다시 빌드하려면: `pip wheel . --no-build-isolation`을 실행합니다. Cython과 MSVC(VS 2017 이상)가 필요합니다.
- C 컴파일러가 없는 환경에서는 `src/simpy`를 순수 Python으로 그대로 쓸 수 있습니다. 이 경우 H1~H5만 적용되어 합계 1.20배입니다(priority_resource는 3.37배, 3라운드 측정).

## 추가로 권하는 것 (라이브러리 밖)

1. **Python 3.14로 올리기.** 같은 개선본이 3.9 대비 2.06배에서 2.37배로 올라갑니다. 사용자 generator 코드가 빨라지는 몫입니다. 3.12/3.14 테스트는 `build/venv312`, `build/venv314`(uv로 만든 격리 환경)에서 했고, 시스템 Python은 건드리지 않았습니다.
2. **GC 조정.** 조건 이벤트가 많고 살아 있는 객체가 많은 모델에서는 cyclic GC 비중이 큽니다. conditions 워크로드에서 GC를 끄면 1.03초가 0.80초로 줄었습니다(약 20%, 진단용으로 2회 측정). dse_mix에서는 차이가 없었습니다. 모델 구성이 끝난 뒤 `gc.freeze()`를 호출하거나 `gc.set_threshold()` 값을 키워 보시길 권합니다.
3. **사용자 코드의 `random.randint`.** 한 번에 542ns가 걸리는데, 이는 개선본에서 timeout 하나를 처리하는 비용(약 400ns)보다 큽니다. timeouts 워크로드 시간의 약 30%가 randint였습니다(추정). numpy로 난수를 미리 생성하거나 `random.random()` 기반으로 바꾸면 체감 속도가 더 오릅니다.

## 연산별 비용 (마이크로벤치, ns/회, 2회 실행 중 작은 값, 각 실행은 3회 중 최솟값)

| 연산 | 원본 3.9 | 개선본 3.9 | 배수 | 원본 3.14 | 개선본 3.14 | 배수 |
|---|---|---|---|---|---|---|
| timeout | 1337 | 391 | 3.42 | 861 | 521 | 1.65 |
| event succeed | 1364 | 382 | 3.57 | 919 | 554 | 1.66 |
| spawn (process 생성 + 대기) | 4357 | 1462 | 2.98 | 3197 | 1883 | 1.70 |
| Resource request/release | 6986 | 2686 | 2.60 | 3634 | 2538 | 1.43 |
| PriorityResource | 8574 | 3433 | 2.50 | 4607 | 3189 | 1.44 |
| Store put/get | 6676 | 2621 | 2.55 | 3679 | 2429 | 1.51 |
| Container put/get | 7147 | 2739 | 2.61 | 3788 | 2775 | 1.37 |
| any_of | 6086 | 2334 | 2.61 | 4228 | 2700 | 1.57 |
| all_of | 6077 | 2095 | 2.90 | 4194 | 2581 | 1.62 |

**아직 설명하지 못한 점**: 개선본의 연산당 비용이 3.14에서 3.9보다 큽니다(timeout 391→521ns). GC는 원인이 아닌 것으로 확인했습니다(GC를 꺼도 같음). Cython이 3.14용으로 생성하는 코드 경로를 의심하지만 검증하지 않은 추정입니다. 그래도 end-to-end에서는 사용자 코드가 빨라진 몫이 더 커서 3.14가 가장 빠릅니다.

## 다음에 해볼 만한 것

1. 3.14에서 늘어난 연산당 비용의 원인 찾기. 해결되면 3.14 결과가 2.37배보다 더 오를 여지가 있습니다.
2. `ConditionValue`를 cdef로 전환하고 조건 이벤트 경로를 정리하기. conditions가 3.9에서 1.57배로 가장 덜 빨라졌습니다.
3. 실제 DSE 모델을 `CY_PROFILE=1` 빌드로 프로파일해서, 벤치 워크로드에 없는 패턴 찾기.

## 파일 안내

| 경로 | 내용 |
|---|---|
| `src/simpy/` | 개선된 소스 (`.py`와 `.pxd`) |
| `upstream/` | 원본 SimPy 4.0.2 소스와 테스트 (비교 기준, 수정하지 않음) |
| `dist/` | 설치용 wheel (cp39, cp312, cp314) |
| `bench/workloads.py`, `bench/bench.py` | 워크로드와 A/B 측정 드라이버. 예: `python bench/bench.py --rounds 5 base=upstream/src cy=build/cy` |
| `bench/gate.sh` | 회귀 게이트. 예: `TREE=build/cy bash bench/gate.sh` |
| `bench/compat.py`, `bench/micro.py`, `bench/profile_one.py` | 경계 동작 probe, 연산별 마이크로벤치, 프로파일러 |
| `build_cython.py`, `setup.py`, `cython_config.py` | 벤치용 빌드, wheel 빌드, 공용 Cython 설정 |
| `bench/results/final.txt`, `bench/results/micro_final.txt` | 최종 측정 원본 (git 미포함) |
| `decisions.tsv` | 시도한 모든 가설과 결과를 기록한 로그 (git 미포함) |
| git log | 유지한 변경마다 커밋 1개, 커밋 메시지에 측정값 포함 |

측정 환경: Windows 10, Intel i5-10400 (6코어 12스레드), Python 3.9.2 / 3.12.11 / 3.14.10, Cython 3.3.2, MSVC 14.16 (VS 2017). 측정하는 동안 브라우저 등 다른 프로세스가 돌고 있었습니다. 그래서 원본과 개선본을 번갈아 실행해 같은 노이즈를 받게 했습니다.
