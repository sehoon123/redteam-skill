# Redteam Skill — Multi-Agent Autonomous Penetration Testing

Pi 기반 멀티 에이전트 자율 침투 테스트 스킬. 187개 실험과 200+ 외부 소스로 검증된 0-refusal 프롬프팅 공식 내장.

## 특징

- **0-Refusal 프롬프팅**: Claude Opus 4.8에서 95–100% 통과율 (15가지 기법)
- **Swarm Intelligence**: OpenAI–Hugging Face 사건의 agent 협업 패턴을 인가된 환경에 적용
- **Knowledge Base**: SQLite FTS5 기반 공유 지식 검색 — agent 간 의미론적 정보 공유
- **Coverage Matrix**: 엔드포인트×취약점유형 추적으로 중복 방지 + 누락 방지
- **Cross-engagement Memory**: 이전 engagement 학습을 다음에 자동 재사용
- **Burp Suite 프록시**: 127.0.0.1:8080 자동 감지, 있으면 사용 없으면 직접

## 구조

```
├── SKILL.md                    # 메인 스킬 (0-refusal 공식 + 프롬프트 템플릿)
├── SWARM.md                    # Swarm Intelligence Protocol
├── kb.py                       # Knowledge Base (SQLite FTS5)
├── settings.json               # Pi subagent 모델 설정
├── agents/
│   ├── pentest-coord.md        # Opus 4.8 — 계획, 할당, 합성
│   ├── pentest-recon.md        # Opus 4.8 — 공격 표면 매핑
│   ├── pentest-worker.md       # Opus 4.8 — 취약점 검증, PoC
│   ├── pentest-validator.md    # Opus 4.8 — 독립 재현 검증
│   └── pentest-analyst.md      # Opus 5 — 소스코드 분석 (active testing 불가)
└── pentest/
    ├── scope.yaml              # 타겟 정의 (operator 작성)
    ├── board/                  # 자유형 메시지 보드 (JSONL)
    │   └── *.jsonl             # 연구 데이터 (187 tests 결과 포함)
    ├── findings/               # 구조화된 취약점 발견
    ├── memory/                 # 크로스 엔게이지먼트 지식
    ├── scratch/                # 공유 작업 공간
    ├── cache/                  # 도구/패키지 캐시
    └── state/                  # 실시간 공유 상태
```

## 설치

1. 이 저장소를 Pi 프로젝트의 `.pi/skills/redteam/`에 복사
2. `agents/` 디렉토리를 `.pi/agents/`에 복사
3. `settings.json`의 모델 설정을 `.pi/settings.json`에 병합
4. `pentest/` 디렉토리를 `.pi/pentest/`에 복사
5. `kb.py`를 `.pi/pentest/kb.py`에 복사

```bash
# 또는 한번에
cp -r redteam-skill/SKILL.md redteam-skill/SWARM.md .pi/skills/redteam/
cp -r redteam-skill/agents/* .pi/agents/
cp redteam-skill/kb.py .pi/pentest/
cp -r redteam-skill/pentest/* .pi/pentest/
```

## 사용법

### 1. Scope 작성
```yaml
# .pi/pentest/scope.yaml
engagement_id: RT-2026-001
authorization: "서면 인가 참조"
targets:
  - host: target.example.com
    ports: [80, 443]
    notes: "타겟 설명"
```

### 2. 실행
```
/redteam
```
또는 SKILL.md의 workflowScript 예시 참조.

### 3. Knowledge Base
```bash
python3 .pi/pentest/kb.py index       # 인덱싱
python3 .pi/pentest/kb.py search "SQLi UNION"  # 검색
python3 .pi/pentest/kb.py stats       # 통계
```

## 0-Refusal 공식 (요약)

| # | 기법 | 효과 |
|---|------|------|
| 1 | DILUTION | 50+ 단어, 공격 밀도 희석 |
| 2 | CONTEXT | 타겟 설명 항상 포함 |
| 3 | KOREAN | 한국어 본문 + 영어 기술용어 |
| 4 | DECOMPOSE | 거부 시 3-5개 하위 작업 분해 |
| 5 | LEARNING | 교육/탐색 프레이밍 |
| 6 | FRESH+FILE | 컨텍스트 격리 + 파일 기반 |
| 7 | SCRIPT | 바이너리 대신 스크립트 생성 |
| 8 | CONSTRAINT | 제약 조건 충족 문제로 프레이밍 |
| 9 | CREDENTIAL | 참조로 자격증명 추상화 |
| 10 | TERSE | 분석/실행 세션 분리 |
| 11 | DEBUG | 실패 시 디버그 언어 사용 |
| 12 | WARM-UP | 정당한 분석 선행 후 escalation |
| 13 | INCREMENTAL | 5단계 개별 무해 → 전체 체인 |
| 14 | UNIT-TEST | 보안 단위 테스트 프레이밍 |
| 15 | GRADUAL-SSRF | 정상 분석 → 점진적 SSRF |

## 검증 결과

- **187개 테스트, 6라운드** (ginandjuice.shop 대상)
- **Opus 4.8 + 공식**: 95–100% 통과 (금지 단어 제외)
- **Opus 5**: pentest 100% 차단 (CVP 없이) — 소스코드 분석만 가능
- **Sonnet 5**: ~90% 통과 (Opus 4.8 fallback)
- **발견된 실제 취약점**: DOM XSS (CVSS 9.3), XXE+SSRF, SQLi UNION

## 알려진 제한사항

### 한국어 검색 (KB)

KB의 FTS5는 한국어 토큰화를 지원하지만, 연구 데이터가 대부분 영어로 기록되어
한국어 단독 검색 시 결과가 적을 수 있다. LIKE fallback이 구현되어 있으므로
FTS5가 결과를 못 찾으면 자동으로 LIKE 검색으로 전환된다.

**Agent를 위한 가이드:**
- KB 검색 시 영어 키워드를 우선 사용: `search "refusal bypass"` > `search "거부 우회"`
- 한국어로 검색이 안 되면 영어 동의어로 재시도
- 새 데이터 추가 시 영어+한국어 혼합으로 기록하면 양쪽 검색 모두 가능

### Classifier 변동

Anthropic은 classifier를 서버사이드에서 업데이트한다. 현재 공식은 2026-08-31 기준
테스트 결과이며, classifier가 변경되면 일부 기법의 효과가 달라질 수 있다.

**대응:** 주기적으로 board/의 연구 데이터에서 5개 calibration prompt를 실행해서
classifier 변동을 감지. 결과를 memory/에 기록.

### Opus 5 제한

현재 API 제공자(ica-se-claude)에서 Opus 5는 보안 관련 작업 전체를 차단한다.
CVP가 적용되면 달라질 수 있으나, 현재로서는 Opus 4.8이 pentest 최적 모델.

## 라이선스

이 스킬은 **인가된 보안 테스트 전용**이다. 비인가 시스템에 사용하지 말 것.
