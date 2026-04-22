"""
mapper.py  (v2)
---------------
librosa 기반 자동 리듬 매퍼.

핵심 개선사항
  1. 밀도 제어: onset_strength 전체를 구한 뒤 상위 sensitivity*100% 만 선택
     → 0.1~1.0 사이가 선형적으로 노트 수에 반영됨
  2. 레인 균형: 주파수 대역 softmax 확률 + 레인별 누적 페널티로
     특정 레인 편중 방지
  3. 최소 간격: 같은 레인에 80ms 미만 연속 노트 제거
"""

import numpy as np
import random

try:
    import librosa
    LIBROSA_OK = True
except ImportError:
    LIBROSA_OK = False


# ───────────────────────────────────────────────
# 내부 파라미터
# ───────────────────────────────────────────────
HOP         = 512
MIN_GAP_S   = 0.11   # 같은 레인 최소 간격 (초) -> 너무 조밀한 연타 방지
GLOBAL_GAP  = 0.09   # 전체 최소 간격 (초) -> 1초에 최대 약 11개 노트 
SOFTMAX_T   = 2.0    # 온도: 낮을수록 주파수 지배적, 높을수록 랜덤
BALANCE_W   = 0.35   # 레인 균형 페널티 가중치 (0=없음, 1=완전 균형)


# ───────────────────────────────────────────────
# 공개 API
# ───────────────────────────────────────────────

def generate_notes(audio_path: str, sensitivity: float = 1.0) -> list:
    """
    Parameters
    ----------
    audio_path  : WAV / MP3 등 오디오 파일 경로
    sensitivity : 1.0 으로 고정 (가장 무난한 감도)

    Returns
    -------
    list of {"time": float, "lane": int}
    """
    if not LIBROSA_OK:
        raise RuntimeError("librosa 미설치. 'pip install librosa' 실행 후 재시도하세요.")

    # ── 1. 오디오 로드 ────────────────────────────────────────────────────────
    y, sr = librosa.load(audio_path, mono=True)

    # ── 2. Onset Strength Envelope 계산 ──────────────────────────────────────
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP,
                                              aggregate=np.median)

    # ── 3. BPM 감지 및 동적 난이도 조절 ──────────────────────────────────────
    bpm_arr, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    bpm = float(np.atleast_1d(bpm_arr)[0])

    dynamic_pre = 3
    dynamic_gap = GLOBAL_GAP
    dynamic_min = MIN_GAP_S

    if bpm > 140.0:
        dynamic_pre = 4
        dynamic_gap = 0.12  # 초당 최대 8.3개로 제한 (너무 많은 콤보 방지)
        dynamic_min = 0.15
    if bpm > 180.0:
        dynamic_pre = 5
        dynamic_gap = 0.14  # 빠른 곡은 초당 7개로 더욱 엄격히 제한
        dynamic_min = 0.18

    # 전체 onset 후보 탐지 (동적 파라미터 적용)
    raw_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr, hop_length=HOP,
        backtrack=True,
        delta=0.03,
        wait=int(sr * dynamic_gap / HOP),
        pre_max=dynamic_pre, post_max=dynamic_pre,
        pre_avg=dynamic_pre, post_avg=dynamic_pre,
    )

    if len(raw_frames) == 0:
        return fallback_notes(_duration(audio_path))

    # 추가로 BPM이 아주 빠르면 강도가 약한 잔잔바리 비트 비례 드롭
    keep_ratio = max(0.3, min(1.0, 1.0 - (bpm - 90.0) * 0.008))
    strengths = onset_env[np.clip(raw_frames, 0, len(onset_env)-1)]
    threshold = np.percentile(strengths, max(0.0, (1.0 - keep_ratio) * 100.0))
    selected_mask = strengths >= threshold
    
    sel_frames = raw_frames[selected_mask]
    sel_times  = librosa.frames_to_time(sel_frames, sr=sr, hop_length=HOP)
    # ── 4. Mel Spectrogram 주파수 대역 파워 ─────────────────────────────────
    n_mels  = 128
    S       = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=n_mels,
                                              hop_length=HOP)
    S_db    = librosa.power_to_db(S, ref=np.max)

    # 4레인 → 4 주파수 대역 (저음~고음)
    b = n_mels // 4
    bands = [S_db[:b], S_db[b:2*b], S_db[2*b:3*b], S_db[3*b:]]

    # ── 5. 레인 배정 ─────────────────────────────────────────────────────────
    lane_counts   = [0] * 4          # 레인별 누적 노트 수 (균형용)
    lane_last_t   = [-999.0] * 4     # 레인별 마지막 노트 시각
    global_last_t = -999.0
    notes         = []

    for t, f in zip(sel_times, sel_frames):
        # 전체 최소 간격 체크
        if t - global_last_t < GLOBAL_GAP:
            continue

        col = min(int(f), S_db.shape[1] - 1)

        # 각 대역 평균 파워
        raw_powers = np.array([b_[:, col].mean() for b_ in bands], dtype=float)

        # dB → 선형 변환 후 softmax (온도 파라미터로 spreading 조절)
        linear_p = 10 ** (raw_powers / 20.0)
        logits   = linear_p / SOFTMAX_T
        probs    = _softmax(logits)

        # 레인 균형 페널티 적용
        total_count  = max(1, sum(lane_counts))
        ideal_ratio  = 0.25
        balance_pen  = np.array([
            max(0.0, lane_counts[i] / total_count - ideal_ratio)
            for i in range(4)
        ])
        # 페널티를 확률에서 빼고 재정규화
        adj_probs = probs - BALANCE_W * balance_pen
        adj_probs = np.clip(adj_probs, 1e-6, None)
        adj_probs /= adj_probs.sum()

        # 같은 레인 최소 간격이 안 된 레인 제외
        for i in range(4):
            if t - lane_last_t[i] < dynamic_min:
                adj_probs[i] = 0.0

        if adj_probs.sum() < 1e-9:
            continue  # 모든 레인이 최소 간격 미달 → 건너뜀

        adj_probs /= adj_probs.sum()

        # 확률적 레인 선택 (완전 랜덤 X, 주파수 편향 O, 균형 유지)
        lane = int(np.random.choice(4, p=adj_probs))

        notes.append({"time": float(t), "lane": lane})
        lane_counts[lane] += 1
        lane_last_t[lane]  = t
        global_last_t      = t

    print(f"[mapper] BPM: {bpm:.1f} | 후보={len(raw_frames)} / 최종={len(notes)} "
          f"(keep={keep_ratio*100:.0f}%)")
    _print_lane_dist(lane_counts)

    return notes


# ───────────────────────────────────────────────
# 내부 유틸
# ───────────────────────────────────────────────



def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


def _duration(path: str) -> float:
    """WAV 길이 추정 (fallback용)."""
    try:
        import wave, contextlib
        with contextlib.closing(wave.open(path, 'r')) as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 180.0


def _print_lane_dist(counts: list):
    total = max(1, sum(counts))
    bar   = ["█" * int(c / total * 20) for c in counts]
    labels = ["D(0)", "F(1)", "J(2)", "K(3)"]
    for lbl, cnt, b in zip(labels, counts, bar):
        print(f"  {lbl}: {cnt:4d}  {b}")


def fallback_notes(duration: float, bpm: float = 128.0) -> list:
    """librosa 없을 때 단순 메트로놈 패턴."""
    beat_interval = 60.0 / bpm
    notes, t = [], 0.5
    pattern  = [0, 2, 1, 3, 0, 3, 2, 1, 1, 2, 3, 0]
    i        = 0
    while t < duration - 0.5:
        notes.append({"time": t, "lane": pattern[i % len(pattern)]})
        t += beat_interval / 2
        i += 1
    return notes


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python mapper.py <audio_file> [sensitivity]")
        sys.exit(1)
    sens  = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
    notes = generate_notes(sys.argv[1], sens)
    print(json.dumps(notes[:10], indent=2))
    print(f"총 {len(notes)} 노트")
