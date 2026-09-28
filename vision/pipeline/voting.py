"""Stage 5 - Is it really there? (temporal voting, REQ-03 and REQ-17)

A single frame can be wrong (a red car, a shop board). We confirm a sign
only when the SAME class is the frame's best guess in at least 3 of the
last 5 frames. Once confirmed, it is announced once for that sighting;
the sighting ends only after the sign has been missing for ~1 s, so
driving past one sign never gives two alerts.
"""
from collections import Counter, deque


class TemporalVoter:
    def __init__(self, window=5, needed=3, sighting_gap_frames=15):
        self.window = deque(maxlen=window)
        self.needed = needed
        self.gap = sighting_gap_frames
        self.last_seen = {}        # label -> frame index it last got a vote
        self.announced = set()     # labels already spoken this sighting
        self.frame_idx = 0
        self.first_vote_ts = {}    # label -> capture time of first vote

    def update(self, frame_votes, ts=None):
        """frame_votes: set of labels seen with enough confidence in this frame
        (usually 0 or 1 labels). Returns list of newly confirmed labels."""
        self.frame_idx += 1
        # one vote per frame per label; empty frames push a "nothing" slot
        self.window.append(frozenset(frame_votes))
        for lab in frame_votes:
            if lab not in self.last_seen or self.frame_idx - self.last_seen[lab] > self.gap:
                self.first_vote_ts[lab] = ts
            self.last_seen[lab] = self.frame_idx

        # sightings that have ended can be announced again next time
        for lab in list(self.announced):
            if self.frame_idx - self.last_seen.get(lab, -10**9) > self.gap:
                self.announced.discard(lab)

        counts = Counter(l for votes in self.window for l in votes)
        confirmed = []
        for lab, n in counts.most_common():
            if n >= self.needed and lab not in self.announced:
                self.announced.add(lab)
                confirmed.append(lab)
        return confirmed

    def reset(self):
        self.window.clear()
        self.announced.clear()
        self.last_seen.clear()
