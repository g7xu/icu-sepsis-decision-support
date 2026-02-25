(function() {
    // URLs injected via data attributes on the dock element (resolved server-side by Django)
    var dock    = document.getElementById('sim-dock');
    var URLS = {
        status:  dock.dataset.urlStatus,
        pause:   dock.dataset.urlPause,
        play:    dock.dataset.urlPlay,
        advance: dock.dataset.urlAdvance,
        rewind:  dock.dataset.urlRewind,
        reset:   dock.dataset.urlReset,
    };

    // Seed initial state from hidden element provided by each page via the sim_state block
    const stateEl = document.getElementById('sim-initial-state');
    let autoPlay = stateEl ? stateEl.dataset.autoPlay === 'true' : false;
    let lastHour = stateEl ? parseInt(stateEl.dataset.currentHour, 10) : -1;
    const csrf   = stateEl ? stateEl.dataset.csrf : '';

    const timeEl    = document.getElementById('dock-time');
    const playBtn   = document.getElementById('dock-play');
    const rewindBtn = document.getElementById('dock-rewind');
    const fwdBtn    = document.getElementById('dock-forward');
    const backBtn   = document.getElementById('dock-back');
    const stepBtn   = document.getElementById('dock-fwd');
    const resetBtn  = document.getElementById('dock-reset');
    const speedSel  = document.getElementById('dock-speed');
    const statusEl  = document.getElementById('dock-status');

    // Seed display from page state
    if (stateEl && stateEl.dataset.speedSeconds) {
        speedSel.value = String(Math.round(parseFloat(stateEl.dataset.speedSeconds)) || 5);
    }
    if (stateEl && stateEl.dataset.currentTime) {
        timeEl.textContent = stateEl.dataset.currentTime;
    }
    playBtn.innerHTML = autoPlay ? '&#9646;&#9646; Pause' : '&#9654; Play';

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrf,
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: body || '',
        }).then(r => r.json());
    }

    function setStatus(msg) { statusEl.textContent = msg; }

    // ── Polling ───────────────────────────────────────────────────────────
    let pollTimer = null;
    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(poll, 1000);
    }
    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }
    function poll() {
        fetch(URLS.status)
            .then(r => r.json())
            .then(data => {
                autoPlay = data.auto_play;
                if (data.current_time) timeEl.textContent = data.current_time;
                playBtn.innerHTML = autoPlay ? '&#9646;&#9646; Pause' : '&#9654; Play';
                if (!autoPlay) {
                    stopPolling();
                    location.reload();
                } else if (data.current_hour !== lastHour) {
                    lastHour = data.current_hour;
                    location.reload();
                }
            }).catch(() => {});
    }

    // ── Play / Pause ──────────────────────────────────────────────────────
    playBtn.addEventListener('click', function() {
        if (autoPlay) {
            post(URLS.pause).then(data => {
                autoPlay = false;
                playBtn.innerHTML = '&#9654; Play';
                setStatus('Paused at ' + (data.current_time || ''));
                stopPolling();
                location.reload();
            });
        } else {
            post(URLS.play, 'speed_seconds=' + speedSel.value + '&direction=forward')
                .then(() => {
                    autoPlay = true;
                    playBtn.innerHTML = '&#9646;&#9646; Pause';
                    setStatus('Playing forward\u2026');
                    startPolling();
                });
        }
    });

    // ── Rewind (auto backward) ────────────────────────────────────────────
    rewindBtn.addEventListener('click', function() {
        post(URLS.play, 'speed_seconds=' + speedSel.value + '&direction=backward')
            .then(data => {
                if (data.status === 'already_playing') { setStatus('Pause first.'); return; }
                autoPlay = true;
                playBtn.innerHTML = '&#9646;&#9646; Pause';
                setStatus('Rewinding\u2026');
                startPolling();
            });
    });

    // ── Fast forward (2×) ─────────────────────────────────────────────────
    fwdBtn.addEventListener('click', function() {
        const speed = Math.max(1, parseFloat(speedSel.value) / 2).toString();
        post(URLS.play, 'speed_seconds=' + speed + '&direction=forward')
            .then(data => {
                if (data.status === 'already_playing') { setStatus('Pause first.'); return; }
                autoPlay = true;
                playBtn.innerHTML = '&#9646;&#9646; Pause';
                setStatus('Fast-forward\u2026');
                startPolling();
            });
    });

    // ── Step +1 ───────────────────────────────────────────────────────────
    stepBtn.addEventListener('click', function() {
        stepBtn.disabled = true;
        stepBtn.textContent = '\u23F3';
        setStatus('Advancing 1 hour\u2026');
        post(URLS.advance).then(data => {
            if (data.error) {
                setStatus('Error: ' + data.error);
                stepBtn.disabled = false;
                stepBtn.innerHTML = '+1 &#8594;';
            } else {
                location.reload();
            }
        }).catch(() => {
            setStatus('Network error.');
            stepBtn.disabled = false;
            stepBtn.innerHTML = '+1 &#8594;';
        });
    });

    // ── Step -1 ───────────────────────────────────────────────────────────
    backBtn.addEventListener('click', function() {
        backBtn.disabled = true;
        backBtn.textContent = '\u23F3';
        setStatus('Rewinding 1 hour\u2026');
        post(URLS.rewind).then(data => {
            if (data.error) {
                setStatus('Error: ' + data.error);
                backBtn.disabled = false;
                backBtn.innerHTML = '&#8592; -1';
            } else {
                location.reload();
            }
        }).catch(() => {
            setStatus('Network error.');
            backBtn.disabled = false;
            backBtn.innerHTML = '&#8592; -1';
        });
    });

    // ── Reset ─────────────────────────────────────────────────────────────
    resetBtn.addEventListener('click', function() {
        if (!confirm('Reset simulation? This will delete all simulated patient data.')) return;
        resetBtn.disabled = true;
        stopPolling();
        post(URLS.reset).then(() => location.reload())
            .catch(() => { resetBtn.disabled = false; });
    });

    // Resume polling if auto-play was active on page load
    if (autoPlay) startPolling();
})();
