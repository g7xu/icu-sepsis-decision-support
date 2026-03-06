(function () {

// =============================================================================
// HELPERS
// =============================================================================

function parseJSON(id) {
    try { return JSON.parse(document.getElementById(id).textContent || '[]'); }
    catch (e) { return []; }
}

function parseJSONObj(id) {
    try { return JSON.parse(document.getElementById(id).textContent || '{}'); }
    catch (e) { return {}; }
}

var CHART_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

// =============================================================================
// DATA
// =============================================================================

var predictionData = parseJSON('prediction-history-data');
var sofaSeriesData = parseJSON('sofa-series-data');
var sepsis3Data    = parseJSONObj('sepsis3-data');

var configEl       = document.getElementById('prediction-config');
var modelOnsetHour = configEl ? configEl.dataset.modelOnsetHour : '';
modelOnsetHour     = modelOnsetHour !== '' ? parseInt(modelOnsetHour, 10) : null;

// Chart type labels and colors
var SERIES_CONFIG = {
    pao2fio2ratio_novent: { label: 'PaO2/FiO2 (no vent)', color: '#3182ce', unit: '' },
    pao2fio2ratio_vent:   { label: 'PaO2/FiO2 (vent)',    color: '#805ad5', unit: '' },
    rate_epinephrine:     { label: 'Epinephrine',          color: '#e53e3e', unit: 'mcg/kg/min' },
    rate_norepinephrine:  { label: 'Norepinephrine',       color: '#dd6b20', unit: 'mcg/kg/min' },
    rate_dopamine:        { label: 'Dopamine',             color: '#38a169', unit: 'mcg/kg/min' },
    rate_dobutamine:      { label: 'Dobutamine',           color: '#d69e2e', unit: 'mcg/kg/min' },
};

var currentChartType = 'sepsis_likelihood';

// =============================================================================
// RISK TREND CHART (Sepsis Likelihood)
// =============================================================================

function buildRiskTrendChart(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    if (!predictionData || predictionData.length === 0) {
        container.innerHTML = '<p style="padding:2rem 1rem; color:#718096;">No prediction data available yet. Advance the simulation to generate predictions.</p>';
        return;
    }

    d3.select(container).selectAll('*').remove();

    var M = { top: 40, right: 60, bottom: 58, left: 72 };
    var W = container.offsetWidth || 800;
    var H = 350;
    var iW = W - M.left - M.right;
    var iH = H - M.top - M.bottom;

    // Tooltip
    var ttDiv = d3.select(container)
        .append('div')
        .attr('class', 'd3-tooltip')
        .style('display', 'none');

    var svg = d3.select(container)
        .append('svg')
        .attr('width', W)
        .attr('height', H);

    var root = svg.append('g')
        .attr('transform', 'translate(' + M.left + ',' + M.top + ')');

    // Background
    root.append('rect')
        .attr('width', iW).attr('height', iH)
        .attr('fill', '#fafafa')
        .attr('stroke', '#e2e8f0').attr('stroke-width', 0.5);

    // Scales
    var hours = predictionData.map(function (d) { return d.prediction_hour; });
    var hourLabels = hours.map(function (h) { return (h < 10 ? '0' : '') + h + ':00'; });

    var xScale = d3.scalePoint()
        .domain(hourLabels)
        .range([0, iW]);

    var yScale = d3.scaleLinear()
        .domain([0, 1])
        .range([iH, 0]);

    // Warning bands
    root.append('rect')
        .attr('x', 0).attr('width', iW)
        .attr('y', yScale(0.3)).attr('height', yScale(0) - yScale(0.3))
        .attr('fill', '#48bb78').attr('opacity', 0.08);
    root.append('rect')
        .attr('x', 0).attr('width', iW)
        .attr('y', yScale(0.6)).attr('height', yScale(0.3) - yScale(0.6))
        .attr('fill', '#ed8936').attr('opacity', 0.08);
    root.append('rect')
        .attr('x', 0).attr('width', iW)
        .attr('y', yScale(1)).attr('height', yScale(0.6) - yScale(1))
        .attr('fill', '#e53e3e').attr('opacity', 0.08);

    // Threshold lines
    [0.3, 0.6].forEach(function (threshold) {
        root.append('line')
            .attr('x1', 0).attr('x2', iW)
            .attr('y1', yScale(threshold)).attr('y2', yScale(threshold))
            .attr('stroke', threshold >= 0.6 ? '#e53e3e' : '#ed8936')
            .attr('stroke-width', 1)
            .attr('stroke-dasharray', '5,3')
            .attr('opacity', 0.6);
        root.append('text')
            .attr('x', iW + 4).attr('y', yScale(threshold) + 4)
            .attr('font-size', 9)
            .attr('fill', threshold >= 0.6 ? '#e53e3e' : '#ed8936')
            .attr('font-family', CHART_FONT)
            .text(Math.round(threshold * 100) + '%');
    });

    // Gridlines
    var yAxisG = root.append('g')
        .call(d3.axisLeft(yScale)
            .ticks(5)
            .tickFormat(function (d) { return Math.round(d * 100) + '%'; })
            .tickSizeOuter(0)
            .tickSize(-iW));
    yAxisG.select('.domain').remove();
    yAxisG.selectAll('.tick line')
        .attr('stroke', '#e2e8f0').attr('stroke-dasharray', '2,2');
    yAxisG.selectAll('.tick text')
        .attr('font-size', 10).attr('fill', '#718096');

    // Y label
    root.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -(iH / 2)).attr('y', -M.left + 14)
        .attr('text-anchor', 'middle')
        .attr('font-size', 11).attr('fill', '#4a5568')
        .attr('font-family', CHART_FONT)
        .text('Risk Score');

    // X axis
    var tickEvery = hourLabels.length > 12 ? 3 : (hourLabels.length > 6 ? 2 : 1);
    var tickVals = hourLabels.filter(function (h, i) { return i % tickEvery === 0; });

    var xAxisG = root.append('g')
        .attr('transform', 'translate(0,' + iH + ')')
        .call(d3.axisBottom(xScale).tickValues(tickVals).tickSizeOuter(0));
    xAxisG.select('.domain').attr('stroke', '#cbd5e0');
    xAxisG.selectAll('.tick line').remove();
    xAxisG.selectAll('.tick text').attr('font-size', 10).attr('fill', '#718096');

    root.append('text')
        .attr('x', iW / 2).attr('y', iH + M.bottom - 10)
        .attr('text-anchor', 'middle')
        .attr('font-size', 12).attr('fill', '#4a5568')
        .attr('font-family', CHART_FONT)
        .text('Hour of Day');

    // Risk line
    var lineGen = d3.line()
        .defined(function (d) { return d.risk_score != null; })
        .x(function (d, i) { return xScale(hourLabels[i]); })
        .y(function (d) { return yScale(d.risk_score); });

    root.append('path')
        .datum(predictionData)
        .attr('fill', 'none')
        .attr('stroke', '#3182ce')
        .attr('stroke-width', 2.5)
        .attr('stroke-linejoin', 'round')
        .attr('stroke-linecap', 'round')
        .attr('d', lineGen);

    // Dots
    root.selectAll('.risk-dot')
        .data(predictionData.filter(function (d) { return d.risk_score != null; }))
        .enter().append('circle')
        .attr('cx', function (d) {
            var idx = hours.indexOf(d.prediction_hour);
            return xScale(hourLabels[idx]);
        })
        .attr('cy', function (d) { return yScale(d.risk_score); })
        .attr('r', 4)
        .attr('fill', function (d) {
            if (d.risk_score >= 0.6) return '#e53e3e';
            if (d.risk_score >= 0.3) return '#ed8936';
            return '#38a169';
        })
        .attr('stroke', 'white').attr('stroke-width', 1.5);

    // Vertical marker: model onset
    if (modelOnsetHour !== null) {
        var onsetIdx = hours.indexOf(modelOnsetHour);
        if (onsetIdx >= 0) {
            var onsetX = xScale(hourLabels[onsetIdx]);
            root.append('line')
                .attr('x1', onsetX).attr('x2', onsetX)
                .attr('y1', 0).attr('y2', iH)
                .attr('stroke', '#e53e3e')
                .attr('stroke-width', 2)
                .attr('stroke-dasharray', '6,3');
            root.append('text')
                .attr('x', onsetX + 4).attr('y', 12)
                .attr('font-size', 9).attr('fill', '#e53e3e')
                .attr('font-family', CHART_FONT)
                .text('Model Onset');
        }
    }

    // Vertical marker: sepsis3 onset (sofa_time)
    var sepsis3Hour = null;
    if (sepsis3Data && sepsis3Data.sofa_time) {
        var sofaTime = new Date(sepsis3Data.sofa_time);
        if (!isNaN(sofaTime)) {
            sepsis3Hour = sofaTime.getHours();
            var s3Idx = hours.indexOf(sepsis3Hour);
            if (s3Idx >= 0) {
                var s3X = xScale(hourLabels[s3Idx]);
                root.append('line')
                    .attr('x1', s3X).attr('x2', s3X)
                    .attr('y1', 0).attr('y2', iH)
                    .attr('stroke', '#805ad5')
                    .attr('stroke-width', 2)
                    .attr('stroke-dasharray', '3,3');
                root.append('text')
                    .attr('x', s3X + 4).attr('y', 24)
                    .attr('font-size', 9).attr('fill', '#805ad5')
                    .attr('font-family', CHART_FONT)
                    .text('Sepsis-3 Onset');
            }
        }
    }

    // Legend
    var legG = root.append('g').attr('transform', 'translate(0,' + (-M.top + 8) + ')');
    var legItems = [
        { color: '#3182ce', label: 'Risk Score', dash: null },
    ];
    if (modelOnsetHour !== null) {
        legItems.push({ color: '#e53e3e', label: 'Model Onset', dash: '6,3' });
    }
    if (sepsis3Hour !== null) {
        legItems.push({ color: '#805ad5', label: 'Sepsis-3 Onset', dash: '3,3' });
    }

    legItems.forEach(function (item, idx) {
        var lx = idx * 140;
        legG.append('line')
            .attr('x1', lx).attr('x2', lx + 18)
            .attr('y1', 6).attr('y2', 6)
            .attr('stroke', item.color).attr('stroke-width', 2)
            .attr('stroke-dasharray', item.dash);
        legG.append('circle')
            .attr('cx', lx + 9).attr('cy', 6).attr('r', 3)
            .attr('fill', item.color);
        legG.append('text')
            .attr('x', lx + 22).attr('y', 10)
            .attr('font-size', 10).attr('fill', '#4a5568')
            .attr('font-family', CHART_FONT)
            .text(item.label);
    });

    // Crosshair + tooltip
    var crosshairG = root.append('g').style('display', 'none').style('pointer-events', 'none');
    crosshairG.append('line')
        .attr('class', 'ch-line')
        .attr('y1', 0).attr('y2', iH)
        .attr('stroke', '#1a365d').attr('stroke-width', 1)
        .attr('stroke-dasharray', '4,2').attr('opacity', 0.6);

    var chDot = crosshairG.append('circle')
        .attr('r', 5).attr('fill', '#3182ce')
        .attr('stroke', 'white').attr('stroke-width', 2);

    root.append('rect')
        .attr('width', iW).attr('height', iH)
        .attr('fill', 'transparent')
        .on('mousemove', function (event) {
            var mx = d3.pointer(event)[0];
            var step = hourLabels.length > 1 ? iW / (hourLabels.length - 1) : iW;
            var idx = Math.round(mx / step);
            idx = Math.max(0, Math.min(hourLabels.length - 1, idx));
            var cx = xScale(hourLabels[idx]);

            crosshairG.style('display', null);
            crosshairG.select('.ch-line').attr('x1', cx).attr('x2', cx);

            var d = predictionData[idx];
            if (d && d.risk_score != null) {
                chDot.style('display', null)
                    .attr('cx', cx)
                    .attr('cy', yScale(d.risk_score));
            } else {
                chDot.style('display', 'none');
            }

            var html = '<div class="tt-hour">' + hourLabels[idx] + '</div>';
            if (d) {
                var pct = d.risk_score != null ? (d.risk_score * 100).toFixed(1) + '%' : '\u2013';
                html += '<div class="tt-row">'
                    + '<span class="tt-dot" style="background:#3182ce"></span>'
                    + '<span class="tt-name">Risk Score</span>'
                    + '<span class="tt-val">' + pct + '</span>'
                    + '</div>';
            }
            ttDiv.style('display', 'block').html(html);

            var ttW = ttDiv.node().offsetWidth;
            var ttH2 = ttDiv.node().offsetHeight;
            var tx = cx + M.left + 14;
            var ty = event.offsetY - ttH2 / 2;
            if (tx + ttW > W - 4) tx = cx + M.left - ttW - 14;
            if (ty < 2) ty = 2;
            if (ty + ttH2 > H - 2) ty = H - ttH2 - 2;
            ttDiv.style('left', tx + 'px').style('top', ty + 'px');
        })
        .on('mouseleave', function () {
            crosshairG.style('display', 'none');
            ttDiv.style('display', 'none');
        });
}

// =============================================================================
// GENERIC SERIES CHART (PaO2/FiO2, drug rates)
// =============================================================================

function buildSeriesChart(containerId, fieldKey) {
    var container = document.getElementById(containerId);
    if (!container) return;

    var cfg = SERIES_CONFIG[fieldKey];
    if (!cfg) return;

    // Filter to rows that have data for this field
    var hasData = sofaSeriesData.some(function (d) { return d[fieldKey] != null; });
    if (!hasData || !sofaSeriesData.length) {
        d3.select(container).selectAll('*').remove();
        container.innerHTML = '<p style="padding:2rem 1rem; color:#718096;">No ' + cfg.label + ' data available for this patient.</p>';
        return;
    }

    d3.select(container).selectAll('*').remove();

    var M = { top: 40, right: 40, bottom: 58, left: 72 };
    var W = container.offsetWidth || 800;
    var H = 350;
    var iW = W - M.left - M.right;
    var iH = H - M.top - M.bottom;

    var hourLabels = sofaSeriesData.map(function (d) { return d.hour_label; });

    // Tooltip
    var ttDiv = d3.select(container)
        .append('div')
        .attr('class', 'd3-tooltip')
        .style('display', 'none');

    var svg = d3.select(container)
        .append('svg')
        .attr('width', W)
        .attr('height', H);

    var root = svg.append('g')
        .attr('transform', 'translate(' + M.left + ',' + M.top + ')');

    // Background
    root.append('rect')
        .attr('width', iW).attr('height', iH)
        .attr('fill', '#fafafa')
        .attr('stroke', '#e2e8f0').attr('stroke-width', 0.5);

    // Scales
    var xScale = d3.scalePoint()
        .domain(hourLabels)
        .range([0, iW]);

    var vals = sofaSeriesData.map(function (d) { return d[fieldKey]; }).filter(function (v) { return v != null; });
    var yMin = d3.min(vals) || 0;
    var yMax = d3.max(vals) || 1;
    var yPad = (yMax - yMin) * 0.1 || 0.5;

    var yScale = d3.scaleLinear()
        .domain([Math.max(0, yMin - yPad), yMax + yPad])
        .range([iH, 0]);

    // Gridlines
    var yAxisG = root.append('g')
        .call(d3.axisLeft(yScale)
            .ticks(5)
            .tickSizeOuter(0)
            .tickSize(-iW));
    yAxisG.select('.domain').remove();
    yAxisG.selectAll('.tick line')
        .attr('stroke', '#e2e8f0').attr('stroke-dasharray', '2,2');
    yAxisG.selectAll('.tick text')
        .attr('font-size', 10).attr('fill', '#718096');

    // Y label
    root.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -(iH / 2)).attr('y', -M.left + 14)
        .attr('text-anchor', 'middle')
        .attr('font-size', 11).attr('fill', '#4a5568')
        .attr('font-family', CHART_FONT)
        .text(cfg.label + (cfg.unit ? ' (' + cfg.unit + ')' : ''));

    // X axis
    var tickEvery = hourLabels.length > 12 ? 3 : (hourLabels.length > 6 ? 2 : 1);
    var tickVals = hourLabels.filter(function (h, i) { return i % tickEvery === 0; });

    var xAxisG = root.append('g')
        .attr('transform', 'translate(0,' + iH + ')')
        .call(d3.axisBottom(xScale).tickValues(tickVals).tickSizeOuter(0));
    xAxisG.select('.domain').attr('stroke', '#cbd5e0');
    xAxisG.selectAll('.tick line').remove();
    xAxisG.selectAll('.tick text').attr('font-size', 10).attr('fill', '#718096');

    root.append('text')
        .attr('x', iW / 2).attr('y', iH + M.bottom - 10)
        .attr('text-anchor', 'middle')
        .attr('font-size', 12).attr('fill', '#4a5568')
        .attr('font-family', CHART_FONT)
        .text('Hour of Day');

    // Line
    var lineGen = d3.line()
        .defined(function (d) { return d[fieldKey] != null; })
        .x(function (d, i) { return xScale(hourLabels[i]); })
        .y(function (d) { return yScale(d[fieldKey]); });

    root.append('path')
        .datum(sofaSeriesData)
        .attr('fill', 'none')
        .attr('stroke', cfg.color)
        .attr('stroke-width', 2.5)
        .attr('stroke-linejoin', 'round')
        .attr('stroke-linecap', 'round')
        .attr('d', lineGen);

    // Dots
    root.selectAll('.series-dot')
        .data(sofaSeriesData.filter(function (d) { return d[fieldKey] != null; }))
        .enter().append('circle')
        .attr('cx', function (d) { return xScale(d.hour_label); })
        .attr('cy', function (d) { return yScale(d[fieldKey]); })
        .attr('r', 4)
        .attr('fill', cfg.color)
        .attr('stroke', 'white').attr('stroke-width', 1.5);

    // Legend
    var legG = root.append('g').attr('transform', 'translate(0,' + (-M.top + 8) + ')');
    legG.append('line')
        .attr('x1', 0).attr('x2', 18)
        .attr('y1', 6).attr('y2', 6)
        .attr('stroke', cfg.color).attr('stroke-width', 2);
    legG.append('circle')
        .attr('cx', 9).attr('cy', 6).attr('r', 3)
        .attr('fill', cfg.color);
    legG.append('text')
        .attr('x', 22).attr('y', 10)
        .attr('font-size', 10).attr('fill', '#4a5568')
        .attr('font-family', CHART_FONT)
        .text(cfg.label);

    // Crosshair + tooltip
    var crosshairG = root.append('g').style('display', 'none').style('pointer-events', 'none');
    crosshairG.append('line')
        .attr('class', 'ch-line')
        .attr('y1', 0).attr('y2', iH)
        .attr('stroke', '#1a365d').attr('stroke-width', 1)
        .attr('stroke-dasharray', '4,2').attr('opacity', 0.6);

    var chDot = crosshairG.append('circle')
        .attr('r', 5).attr('fill', cfg.color)
        .attr('stroke', 'white').attr('stroke-width', 2);

    root.append('rect')
        .attr('width', iW).attr('height', iH)
        .attr('fill', 'transparent')
        .on('mousemove', function (event) {
            var mx = d3.pointer(event)[0];
            var step = hourLabels.length > 1 ? iW / (hourLabels.length - 1) : iW;
            var idx = Math.round(mx / step);
            idx = Math.max(0, Math.min(hourLabels.length - 1, idx));
            var cx = xScale(hourLabels[idx]);

            crosshairG.style('display', null);
            crosshairG.select('.ch-line').attr('x1', cx).attr('x2', cx);

            var d = sofaSeriesData[idx];
            if (d && d[fieldKey] != null) {
                chDot.style('display', null)
                    .attr('cx', cx)
                    .attr('cy', yScale(d[fieldKey]));
            } else {
                chDot.style('display', 'none');
            }

            var html = '<div class="tt-hour">' + hourLabels[idx] + '</div>';
            if (d) {
                var val = d[fieldKey] != null ? d[fieldKey].toFixed(2) : '\u2013';
                html += '<div class="tt-row">'
                    + '<span class="tt-dot" style="background:' + cfg.color + '"></span>'
                    + '<span class="tt-name">' + cfg.label + '</span>'
                    + '<span class="tt-val">' + val + '</span>'
                    + '</div>';
            }
            ttDiv.style('display', 'block').html(html);

            var ttW = ttDiv.node().offsetWidth;
            var ttH2 = ttDiv.node().offsetHeight;
            var tx = cx + M.left + 14;
            var ty = event.offsetY - ttH2 / 2;
            if (tx + ttW > W - 4) tx = cx + M.left - ttW - 14;
            if (ty < 2) ty = 2;
            if (ty + ttH2 > H - 2) ty = H - ttH2 - 2;
            ttDiv.style('left', tx + 'px').style('top', ty + 'px');
        })
        .on('mouseleave', function () {
            crosshairG.style('display', 'none');
            ttDiv.style('display', 'none');
        });
}

// =============================================================================
// CHART DISPATCHER + RADIO BUTTONS + RESIZE
// =============================================================================

function renderCurrentChart() {
    if (currentChartType === 'sepsis_likelihood') {
        buildRiskTrendChart('chart-risk-trend');
    } else {
        buildSeriesChart('chart-risk-trend', currentChartType);
    }
}

// Radio button listeners
var radios = document.querySelectorAll('input[name="chart-type"]');
radios.forEach(function (radio) {
    radio.addEventListener('change', function () {
        currentChartType = this.value;
        renderCurrentChart();
    });
});

// Responsive resize with debounce
var resizeTimer;
window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderCurrentChart, 200);
});

// Initial render
renderCurrentChart();

})();
