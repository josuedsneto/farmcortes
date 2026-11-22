"""Deterministic chat and audio peak detection on recording-relative seconds."""
import math
import re
import statistics
from collections import defaultdict

BURST = re.compile(r'(?i)(?:k{4,}|\b(?:clipa|mds|lul|kekw|omegalul|pogchamp|monkas)\b|\?{4,}|d:)')

def detect(messages, rms, *, min_messages=8, burst_threshold=4, dedupe_seconds=45):
    """messages: (seconds, text); rms: (seconds, linear RMS)."""
    bins = defaultdict(list)
    for t, text in messages:
        if t >= 0: bins[int(t // 10)].append(text)
    candidates = []
    for bucket, texts in sorted(bins.items()):
        baseline = statistics.median([len(bins.get(i, [])) for i in range(max(0,bucket-30), bucket)] or [0])
        count = len(texts)
        burst = sum(bool(BURST.search(text)) for text in texts)
        reasons = []
        if count >= min_messages and count > 3 * baseline: reasons.append('chat')
        if burst >= burst_threshold: reasons.append('termos')
        if reasons: candidates.append((bucket*10+5, reasons, max(count/max(1,baseline), burst/max(1,burst_threshold))))
    ordered = sorted((float(t), float(v)) for t,v in rms if t >= 0 and math.isfinite(v) and v >= 0)
    for i,(t,v) in enumerate(ordered):
        history = [x for ht,x in ordered[:i] if t-300 <= ht < t]
        if len(history) < 5: continue
        med = statistics.median(history)
        mad = statistics.median(abs(x-med) for x in history)
        if v > med + 3*mad and v > 0:
            candidates.append((t, ['volume'], (v-med)/max(mad, 0.001)))
    # Group nearby detections and retain the strongest timestamp while combining evidence.
    groups = []
    for t, reasons, score in sorted(candidates):
        if groups and t-groups[-1][0][0] < dedupe_seconds: groups[-1].append((t,reasons,score))
        else: groups.append([(t,reasons,score)])
    return [{'timestamp': max(g,key=lambda x:x[2])[0], 'reasons': sorted({r for _,rs,_ in g for r in rs}), 'score': max(x[2] for x in g)+len({r for _,rs,_ in g for r in rs})-1} for g in groups]
