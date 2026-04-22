"""
game.py
-------
pygame 기반 4레인 리듬게임.

실행:
  python game.py <audio_file> [notes_json]

  notes_json 생략 시 mapper.py 로 자동 생성.

키 바인딩:
  D  F  J  K  →  레인 0 1 2 3
  ESC  → 종료
  R   → 재시작
"""

import sys, os, json, time, math
import pygame
import numpy as np

# ── mapper 임포트 (fallback 포함) ───────────────────────────────────────────
try:
    from mapper import generate_notes, fallback_notes
    MAPPER_OK = True
except ImportError:
    MAPPER_OK = False

# ═══════════════════════════════════════════════════════════════════════════════
# 설정 상수
# ═══════════════════════════════════════════════════════════════════════════════
W, H          = 800, 900
FPS           = 120

LANE_COUNT    = 4
LANE_KEYS     = [pygame.K_d, pygame.K_f, pygame.K_j, pygame.K_k]
KEY_LABELS    = ["D", "F", "J", "K"]

NOTE_SPEED    = 500          # 픽셀/초
NOTE_W        = 110
NOTE_H        = 28
JUDGE_Y       = H - 140      # 판정선 Y
HIT_WINDOW    = 0.11         # ±110ms = GOOD  (±55ms = PERFECT)
PERFECT_WIN   = 0.055

# 색상 팔레트 ─────────────────────────────────────────────────────────────────
C_BG          = (10,  10,  18)
C_LANE_BG     = (20,  20,  36)
C_LANE_LINE   = (50,  50,  90)
C_JUDGE_LINE  = (255, 220,  60)
C_NOTE        = [(100, 180, 255), (100, 255, 160),
                 (255, 150, 100), (220, 100, 255)]
C_NOTE_GLOW   = [(160, 210, 255), (160, 255, 210),
                 (255, 200, 160), (255, 160, 255)]
C_KEY_IDLE    = (35,  35,  65)
C_KEY_PRESS   = [(80, 140, 220), (80, 220, 140),
                 (220, 120, 80), (180, 80, 220)]
C_PERFECT     = (255, 240,  80)
C_GOOD        = (80,  220, 255)
C_MISS        = (220,  60,  60)
C_WHITE       = (255, 255, 255)
C_GRAY        = (130, 130, 160)

# ═══════════════════════════════════════════════════════════════════════════════
# 유틸
# ═══════════════════════════════════════════════════════════════════════════════

def load_notes(audio_path: str, sensitivity: float = 0.55) -> list[dict]:
    if MAPPER_OK:
        try:
            print("[매퍼] 오디오 분석 중... (처음엔 시간이 걸립니다)")
            return generate_notes(audio_path, sensitivity)
        except Exception as e:
            print(f"[매퍼] 오류: {e}  →  fallback 패턴 사용")
    # fallback: 단순 비트 패턴
    import wave, contextlib
    duration = 180.0
    try:
        with contextlib.closing(wave.open(audio_path, 'r')) as wf:
            duration = wf.getnframes() / wf.getframerate()
    except Exception:
        pass
    if MAPPER_OK:
        return fallback_notes(duration)
    # mapper 자체가 없는 경우
    return _basic_fallback(duration)


def _basic_fallback(duration: float) -> list[dict]:
    notes, t, cycle = [], 0.5, [0, 2, 1, 3, 0, 3, 1, 2]
    i = 0
    while t < duration - 0.5:
        notes.append({"time": t, "lane": cycle[i % len(cycle)]})
        t += 60 / 130 / 2
        i += 1
    return notes


# ═══════════════════════════════════════════════════════════════════════════════
# 노트 클래스
# ═══════════════════════════════════════════════════════════════════════════════
class Note:
    def __init__(self, time: float, lane: int, lane_x: int):
        self.time       = time
        self.lane       = lane
        self.x          = lane_x
        self.y          = -NOTE_H
        self.hit        = False   # 'PERFECT' / 'GOOD' / 'MISS' / False
        self.alpha      = 255
        self.dead       = False

    def update(self, song_time: float, dt: float):
        if self.hit:
            self.alpha -= 600 * dt
            if self.alpha <= 0:
                self.dead = True
            return
        # 판정선까지 남은 시간으로 Y 계산
        remaining = self.time - song_time
        self.y = JUDGE_Y - remaining * NOTE_SPEED

        # 판정선을 많이 넘어가면 MISS 처리
        if self.y > JUDGE_Y + NOTE_H * 3 and not self.hit:
            self.hit = "MISS"

    def draw(self, surf: pygame.Surface, lane_x: int):
        if self.dead:
            return
        col = C_NOTE[self.lane]
        glow = C_NOTE_GLOW[self.lane]
        alpha = max(0, min(255, int(self.alpha)))

        rect = pygame.Rect(lane_x - NOTE_W // 2, int(self.y) - NOTE_H // 2,
                           NOTE_W, NOTE_H)

        # 글로우 레이어
        glow_surf = pygame.Surface((NOTE_W + 16, NOTE_H + 16), pygame.SRCALPHA)
        pygame.draw.rect(glow_surf, (*glow, alpha // 3),
                         glow_surf.get_rect(), border_radius=10)
        surf.blit(glow_surf, (rect.x - 8, rect.y - 8))

        # 메인 노트
        note_surf = pygame.Surface((NOTE_W, NOTE_H), pygame.SRCALPHA)
        pygame.draw.rect(note_surf, (*col, alpha),
                         note_surf.get_rect(), border_radius=8)
        # 하이라이트 줄
        pygame.draw.rect(note_surf, (*C_WHITE, alpha // 2),
                         pygame.Rect(8, 2, NOTE_W - 16, 4), border_radius=4)
        surf.blit(note_surf, rect.topleft)


# ═══════════════════════════════════════════════════════════════════════════════
# 히트 이펙트
# ═══════════════════════════════════════════════════════════════════════════════
class HitEffect:
    def __init__(self, x: int, y: int, verdict: str, pts: int = 0):
        self.x       = x
        self.y       = y
        self.verdict = verdict
        self.life    = 0.6   # 초
        self.elapsed = 0.0
        col_map = {"PERFECT": C_PERFECT, "GOOD": C_GOOD, "MISS": C_MISS}
        self.color   = col_map.get(verdict, C_WHITE)

    def update(self, dt: float):
        self.elapsed += dt

    @property
    def alive(self):
        return self.elapsed < self.life

    def draw(self, surf: pygame.Surface, font: pygame.font.Font, font_small: pygame.font.Font):
        progress = self.elapsed / self.life
        alpha = int(255 * (1 - progress) ** 1.5)
        offset_y = -60 * progress
        r, g, b = self.color
        text = font.render(self.verdict, True, (r, g, b))
        text.set_alpha(alpha)
        rect = text.get_rect(center=(self.x, int(self.y + offset_y)))
        surf.blit(text, rect)

class ScorePopup:
    def __init__(self, x: int, y: int, pts: int):
        self.x = x
        self.y = y
        self.pts = pts
        self.life = 0.5
        self.elapsed = 0.0

    def update(self, dt: float):
        self.elapsed += dt

    @property
    def alive(self):
        return self.elapsed < self.life

    def draw(self, surf: pygame.Surface, font: pygame.font.Font):
        progress = self.elapsed / self.life
        alpha = int(255 * (1 - progress) ** 1.5)
        offset_y = -30 * progress
        txt = font.render(f"+{self.pts}", True, C_PERFECT)
        txt.set_alpha(alpha)
        rect = txt.get_rect(center=(self.x, int(self.y + offset_y)))
        surf.blit(txt, rect)


# ═══════════════════════════════════════════════════════════════════════════════
# 파티클 스파크
# ═══════════════════════════════════════════════════════════════════════════════
class Spark:
    def __init__(self, x: int, y: int, color):
        self.x = x + np.random.randint(-30, 30)
        self.y = y
        vr = np.random.uniform(80, 220)
        ang = np.random.uniform(-math.pi / 2 - 0.8, -math.pi / 2 + 0.8)
        self.vx = vr * math.cos(ang)
        self.vy = vr * math.sin(ang)
        self.color = color
        self.life = np.random.uniform(0.25, 0.5)
        self.elapsed = 0.0
        self.size = np.random.randint(3, 7)

    @property
    def alive(self):
        return self.elapsed < self.life

    def update(self, dt: float):
        self.elapsed += dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += 500 * dt   # 중력

    def draw(self, surf: pygame.Surface):
        progress = self.elapsed / self.life
        alpha = int(255 * (1 - progress))
        r, g, b = self.color
        s = pygame.Surface((self.size * 2, self.size * 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (r, g, b, alpha), (self.size, self.size), self.size)
        surf.blit(s, (int(self.x) - self.size, int(self.y) - self.size))


# ═══════════════════════════════════════════════════════════════════════════════
# 메인 게임 클래스
# ═══════════════════════════════════════════════════════════════════════════════
class RhythmGame:
    def __init__(self, screen: pygame.Surface, audio_path: str, notes: list[dict],
                 sensitivity: float = 1.0, entry_id: str = None):
        self.screen      = screen
        self.audio_path  = audio_path
        self.raw_notes   = notes
        self.sensitivity = sensitivity
        self.entry_id    = entry_id

        # 레인 X좌표
        margin = (W - LANE_COUNT * NOTE_W) // (LANE_COUNT + 1)
        self.lane_xs = [margin + (NOTE_W + margin) * i + NOTE_W // 2
                        for i in range(LANE_COUNT)]

        # 폰트
        pygame.font.init()
        self.font_big   = pygame.font.SysFont("segoeui", 42, bold=True)
        self.font_mid   = pygame.font.SysFont("segoeui", 28, bold=True)
        self.font_small = pygame.font.SysFont("segoeui", 22)
        self.font_key   = pygame.font.SysFont("segoeui", 30, bold=True)
        self.font_score = pygame.font.SysFont("segoeui", 36, bold=True)

        self.reset()

    # ── 초기화 ───────────────────────────────────────────────────────────────
    def reset(self):
        self.notes    = [Note(n["time"], n["lane"], self.lane_xs[n["lane"]])
                         for n in self.raw_notes]
        self.note_idx   = 0
        self.active     = []
        self.effects    = []
        self.sparks     = []
        self.score_pops = []
        self.key_press  = [False] * LANE_COUNT
        self.key_anim   = [0.0]  * LANE_COUNT
        self.score      = 0
        self.combo      = 0
        self.max_combo  = 0
        self.perfect    = 0
        self.good       = 0
        self.miss       = 0
        self.song_time  = 0.0
        self.start_time = None
        self.finished   = False

        # ── READY 상태 변수 ──────────────────────────────
        # "ready" → "countdown" → "playing" → "finished"
        self.state          = "ready"
        self.keys_confirmed = [False] * LANE_COUNT   # 각 키를 한 번이라도 눌렀는지
        self.key_flash      = [0.0]  * LANE_COUNT    # 눌림 플래시 타이머
        self.countdown_t    = 3.0                    # 초 카운트다운
        self._audio_started = False

        pygame.mixer.music.stop()
        pygame.mixer.music.load(self.audio_path)

    # ── 메인 루프 ─────────────────────────────────────────────────────────────
    def run(self):
        clock = pygame.time.Clock()
        while True:
            dt = clock.tick(FPS) / 1000.0
            dt = min(dt, 0.05)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return "QUIT"
                result = self._handle_event(event)
                if result:
                    return result

            if self.state == "ready":
                self._update_ready(dt)
                self._draw_ready()
            elif self.state == "countdown":
                self._update_countdown(dt)
                self._draw_countdown()
            else:
                self._update(dt)
                self._draw()

            pygame.display.flip()

    # ── 이벤트 처리 ───────────────────────────────────────────────────────────
    def _handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return "QUIT"
            if event.key == pygame.K_r:
                self.reset()
                return None
            if event.key == pygame.K_SPACE and self.state == "finished":
                self.reset()
                return None
            for i, k in enumerate(LANE_KEYS):
                if event.key == k:
                    self.key_press[i] = True
                    # READY 상태에서 키 확인 등록
                    if self.state == "ready":
                        self.keys_confirmed[i] = True
                        self.key_flash[i] = 1.0
                    elif self.state == "playing" and not self.finished:
                        self._try_hit(i)
        if event.type == pygame.KEYUP:
            for i, k in enumerate(LANE_KEYS):
                if event.key == k:
                    self.key_press[i] = False
        return None

    # ── READY 업데이트 ────────────────────────────────────────────────────────
    def _update_ready(self, dt: float):
        for i in range(LANE_COUNT):
            if self.key_flash[i] > 0:
                self.key_flash[i] = max(0.0, self.key_flash[i] - dt * 3)
            # 키 누름 애니
            target = 1.0 if self.key_press[i] else 0.0
            self.key_anim[i] += (target - self.key_anim[i]) * min(1.0, dt * 18)

        if all(self.keys_confirmed):
            self.state = "countdown"
            self.countdown_t = 3.0
            self.song_time = -3.0

    # ── 연산 분리 (카운트다운 중에도 노트 떨어지게) ───────────────────────────
    def _update_notes_falling(self, dt: float):
        # 판정선에서 노트 떨어지는 시간을 감안하여 1초 여유 있게 투입
        fall_time = (JUDGE_Y + NOTE_H) / NOTE_SPEED
        while self.note_idx < len(self.notes):
            n = self.notes[self.note_idx]
            if n.time - self.song_time < fall_time + 1.0:
                self.active.append(n)
                self.note_idx += 1
            else:
                break

        for note in self.active:
            note.update(self.song_time, dt)
            if note.hit == "MISS" and not hasattr(note, '_miss_counted'):
                note._miss_counted = True
                self.miss += 1
                self.combo = 0
                x = self.lane_xs[note.lane]
                self.effects.append(HitEffect(x, JUDGE_Y, "MISS"))

        self.active = [n for n in self.active if not n.dead]

    # ── COUNTDOWN 업데이트 ────────────────────────────────────────────────────
    def _update_countdown(self, dt: float):
        for i in range(LANE_COUNT):
            target = 1.0 if self.key_press[i] else 0.0
            self.key_anim[i] += (target - self.key_anim[i]) * min(1.0, dt * 18)

        self.countdown_t -= dt
        self.song_time = -self.countdown_t
        
        self._update_notes_falling(dt)

        if self.countdown_t <= 0:
            self.state = "playing"

    # ── 판정 ─────────────────────────────────────────────────────────────────
    def _try_hit(self, lane: int):
        best = None
        best_diff = 999.0
        for note in self.active:
            if note.lane == lane and not note.hit:
                diff = abs(note.time - self.song_time)
                if diff < best_diff:
                    best_diff = diff
                    best = note
        if best is None:
            return
        if best_diff <= PERFECT_WIN:
            verdict = "PERFECT"
            pts = 300
        elif best_diff <= HIT_WINDOW:
            verdict = "GOOD"
            pts = 100
        else:
            return
        best.hit = verdict
        self.combo += 1
        self.max_combo = max(self.max_combo, self.combo)
        actual_pts = pts * (1 + self.combo // 10)
        self.score += actual_pts
        if verdict == "PERFECT":
            self.perfect += 1
        else:
            self.good += 1
        x = self.lane_xs[lane]
        self.effects.append(HitEffect(x, JUDGE_Y, verdict, actual_pts))
        self.score_pops.append(ScorePopup(W // 2 + 100, 30, actual_pts))
        for _ in range(12):
            self.sparks.append(Spark(x, JUDGE_Y, C_NOTE_GLOW[lane]))

    # ── 업데이트 ──────────────────────────────────────────────────────────────
    def _update(self, dt: float):
        if self.finished:
            return

        # 오디오 시작
        if not self._audio_started:
            pygame.mixer.music.play()
            self.start_time = time.perf_counter()
            self.song_time = 0.0
            self._audio_started = True

        self.song_time = time.perf_counter() - self.start_time

        self._update_notes_falling(dt)

        # 키 애니
        for i in range(LANE_COUNT):
            target = 1.0 if self.key_press[i] else 0.0
            self.key_anim[i] += (target - self.key_anim[i]) * min(1.0, dt * 18)

        # 이펙트 / 스파크
        for e in self.effects:
            e.update(dt)
        self.effects = [e for e in self.effects if e.alive]
        for s in self.sparks:
            s.update(dt)
        self.sparks = [s for s in self.sparks if s.alive]
        for p in self.score_pops:
            p.update(dt)
        self.score_pops = [p for p in self.score_pops if p.alive]

        # 종료 판정
        all_processed = (self.note_idx >= len(self.notes) and
                         all(n.hit for n in self.active))
        if all_processed and not pygame.mixer.music.get_busy():
            self.finished = True
            self._save_score()

    def _save_score(self):
        if not self.entry_id: return
        try:
            lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.json")
            if os.path.exists(lib_path):
                with open(lib_path, "r", encoding="utf-8") as f:
                    lib = json.load(f)
                for entry in lib:
                    if entry.get("id") == self.entry_id:
                        scores = entry.get("scores", [])
                        scores.append(self.score)
                        scores = sorted(scores, reverse=True)[:10]
                        entry["scores"] = scores
                        self.top_scores = scores
                        break
                with open(lib_path, "w", encoding="utf-8") as f:
                    json.dump(lib, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Score Save Error] {e}")

    # ── 그리기 ────────────────────────────────────────────────────────────────
    def _draw_ready(self):
        self.screen.fill(C_BG)
        self._draw_lanes()
        self._draw_judge_line()
        self._draw_keys()
        
        # READY 텍스트
        title = self.font_big.render("PRESS ALL 4 KEYS TO START", True, C_WHITE)
        self.screen.blit(title, (W // 2 - title.get_width() // 2, H // 2 - 100))
        
        # 확인된 키 표시
        for i in range(LANE_COUNT):
            if self.keys_confirmed[i]:
                color = C_PERFECT
                if self.key_flash[i] > 0:
                    color = tuple(min(255, int(c + (255 - c) * self.key_flash[i])) for c in color)
                txt = self.font_mid.render("READY", True, color)
                self.screen.blit(txt, (self.lane_xs[i] - txt.get_width() // 2, JUDGE_Y - 80))

    def _draw_countdown(self):
        self.screen.fill(C_BG)
        self._draw_lanes()
        self._draw_judge_line()
        for note in self.active:
            note.draw(self.screen, self.lane_xs[note.lane])
        self._draw_keys()
        
        # 카운트다운 숫자
        cnt = math.ceil(self.countdown_t)
        if cnt > 0:
            txt = self.font_big.render(str(cnt), True, C_PERFECT)
            scale = 1.0 + 0.3 * (cnt - self.countdown_t)
            w, h = txt.get_size()
            scaled_txt = pygame.transform.scale(txt, (int(w * scale), int(h * scale)))
            scaled_txt.set_alpha(int(255 * (1.0 - (cnt - self.countdown_t))))
            self.screen.blit(scaled_txt, scaled_txt.get_rect(center=(W // 2, H // 2 - 100)))

    def _draw(self):
        self.screen.fill(C_BG)
        self._draw_lanes()
        self._draw_judge_line()
        for note in self.active:
            note.draw(self.screen, self.lane_xs[note.lane])
        for s in self.sparks:
            s.draw(self.screen)
        for e in self.effects:
            e.draw(self.screen, self.font_big, self.font_small)
        self._draw_keys()
        self._draw_hud()
        if self.finished:
            self._draw_result()

    def _draw_lanes(self):
        lane_total_w = LANE_COUNT * NOTE_W + (LANE_COUNT - 1) * (self.lane_xs[1] - self.lane_xs[0] - NOTE_W)
        left_x = self.lane_xs[0] - NOTE_W // 2
        total_w = self.lane_xs[-1] + NOTE_W // 2 - left_x

        # 레인 배경
        bg = pygame.Surface((total_w, H), pygame.SRCALPHA)
        bg.fill((*C_LANE_BG, 180))
        self.screen.blit(bg, (left_x, 0))

        # 레인 구분선
        for i in range(LANE_COUNT + 1):
            x = left_x + i * (total_w // LANE_COUNT)
            pygame.draw.line(self.screen, C_LANE_LINE, (x, 0), (x, H), 1)

        # 주기적인 가로 스캔라인 (장식)
        t = self.song_time if self._audio_started else 0
        for row in range(0, H, 80):
            y = (row + int(t * 60)) % H
            scan = pygame.Surface((total_w, 1), pygame.SRCALPHA)
            scan.fill((255, 255, 255, 12))
            self.screen.blit(scan, (left_x, y))

    def _draw_judge_line(self):
        left_x = self.lane_xs[0] - NOTE_W // 2
        right_x = self.lane_xs[-1] + NOTE_W // 2
        # 글로우
        for offset, alpha in [(4, 40), (2, 80), (0, 255)]:
            col = (*C_JUDGE_LINE, alpha)
            s = pygame.Surface((right_x - left_x, 3 + offset * 2), pygame.SRCALPHA)
            pygame.draw.rect(s, col, s.get_rect())
            self.screen.blit(s, (left_x, JUDGE_Y - 1 - offset))

    def _draw_keys(self):
        key_h = 70
        key_y = JUDGE_Y + 12
        for i in range(LANE_COUNT):
            x = self.lane_xs[i]
            ka = self.key_anim[i]
            col = tuple(int(C_KEY_IDLE[j] + (C_KEY_PRESS[i][j] - C_KEY_IDLE[j]) * ka)
                        for j in range(3))
            rect = pygame.Rect(x - NOTE_W // 2 + 4, key_y,
                               NOTE_W - 8, int(key_h - 6 * ka))
            s = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(s, (*col, 230), s.get_rect(), border_radius=10)
            self.screen.blit(s, rect.topleft)

            label = self.font_key.render(KEY_LABELS[i], True, C_WHITE)
            lr = label.get_rect(center=(x, key_y + key_h // 2 - 3))
            self.screen.blit(label, lr)

    def _draw_hud(self):
        # 스코어
        sc_txt = self.font_score.render(f"{self.score:,}", True, C_WHITE)
        self.screen.blit(sc_txt, (W // 2 - sc_txt.get_width() // 2, 18))

        # 콤보
        if self.combo >= 2:
            combo_col = C_PERFECT if self.combo >= 10 else C_GOOD
            ct = self.font_mid.render(f"{self.combo} COMBO", True, combo_col)
            self.screen.blit(ct, (W // 2 - ct.get_width() // 2, 62))

        for p in self.score_pops:
            p.draw(self.screen, self.font_small)

        # P/G/M 통계
        stats = f"P:{self.perfect}  G:{self.good}  M:{self.miss}"
        st = self.font_small.render(stats, True, C_GRAY)
        self.screen.blit(st, (18, 18))

        # 경과 시간
        if self._audio_started:
            elapsed = int(self.song_time)
            et = self.font_small.render(f"{elapsed // 60}:{elapsed % 60:02d}", True, C_GRAY)
            self.screen.blit(et, (W - et.get_width() - 18, 18))

    def _draw_result(self):
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        total = self.perfect + self.good + self.miss
        acc = (self.perfect * 100 + self.good * 50) / max(1, total * 100) * 100

        lines = [
            ("RESULT", self.font_big, C_JUDGE_LINE),
            (f"SCORE  {self.score:,}", self.font_mid, C_WHITE),
            (f"MAX COMBO  {self.max_combo}", self.font_mid, C_GOOD),
            (f"PERFECT  {self.perfect}", self.font_mid, C_PERFECT),
            (f"GOOD     {self.good}", self.font_mid, C_GOOD),
            (f"MISS     {self.miss}", self.font_mid, C_MISS),
            (f"ACCURACY {acc:.1f}%", self.font_mid, C_WHITE),
            ("", self.font_small, C_GRAY),
        ]
        
        if hasattr(self, "top_scores") and self.top_scores:
            lines.append(("--- TOP SCORES ---", self.font_small, C_GRAY))
            max_idx = min(3, len(self.top_scores))
            for idx in range(max_idx):
                col = C_PERFECT if idx == 0 else C_WHITE
                lines.append((f"#{idx+1}  {self.top_scores[idx]:,}", self.font_small, col))
        lines.extend([
            ("", self.font_small, C_GRAY),
            ("[SPACE / R] RESTART   [ESC] EXIT", self.font_small, C_GRAY),
        ])

        y = H // 2 - 200
        for text, font, col in lines:
            surf = font.render(text, True, col)
            self.screen.blit(surf, (W // 2 - surf.get_width() // 2, y))
            y += surf.get_height() + 12
# ═══════════════════════════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    """
    Usage:
      game.py <audio_file> [sensitivity] [notes_json] [entry_id]

    notes_json: 라이브러리에서 캐시된 노트맵 JSON 경로.
                전달 시 mapper 분석을 건너뛰어 즉시 시작.
    entry_id: score 저장을 위한 라이브러리 ID.
    """
    if len(sys.argv) < 2:
        print("Usage: python game.py <audio_file> [sensitivity] [notes_json] [entry_id]")
        sys.exit(1)

    audio_path  = sys.argv[1]
    sensitivity = 1.0  # 고정
    notes_json  = sys.argv[3] if len(sys.argv) > 3 else None
    entry_id    = sys.argv[4] if len(sys.argv) > 4 else None

    if not os.path.exists(audio_path):
        print(f"파일 없음: {audio_path}")
        sys.exit(1)

    # ── 노트 로딩 ──────────────────────────────────────────────────────────────
    if notes_json and os.path.exists(notes_json):
        print(f"[게임] 캐시된 노트맵 로딩: {os.path.basename(notes_json)}")
        with open(notes_json, "r", encoding="utf-8") as f:
            notes = json.load(f)
        print(f"[게임] {len(notes)}개 노트 (캐시)")
    else:
        print("[게임] 노트 분석 중... (처음엔 시간이 걸립니다)")
        notes = load_notes(audio_path, sensitivity)
        print(f"[게임] {len(notes)}개 노트 생성됨.")

    pygame.init()
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("4-Lane Rhythm Game")

    while True:
        game = RhythmGame(screen, audio_path, notes, sensitivity, entry_id)
        result = game.run()
        if result == "QUIT":
            break

    pygame.quit()


if __name__ == "__main__":
    main()
