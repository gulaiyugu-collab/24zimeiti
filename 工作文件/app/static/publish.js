/* 发布复盘：登记 → 发布 → 回填 → 复盘。所有用户内容均通过 textContent 渲染。 */
(function () {
  'use strict';

  var SCORE_DIMENSIONS = [
    '问题强度', '受众匹配', '首屏钩子', '具体证据', '表达清晰', '可传播性', '执行成本'
  ];
  var METRICS = [
    { key: 'impressions', label: '曝光量', unit: '次', integer: true },
    { key: 'views', label: '播放量', unit: '次', integer: true },
    { key: 'likes', label: '点赞数', unit: '次', integer: true },
    { key: 'comments', label: '评论数', unit: '次', integer: true },
    { key: 'shares', label: '分享数', unit: '次', integer: true },
    { key: 'followers', label: '新增粉丝', unit: '人', integer: true },
    { key: 'retention', label: '留存率', unit: '%', integer: false, max: 100 }
  ];
  var METRIC_BY_KEY = METRICS.reduce(function (map, metric) {
    map[metric.key] = metric;
    return map;
  }, {});
  var STATUS_LABEL = {
    predicted: '已登记',
    published: '已发布',
    measured: '已回填',
    reviewed: '已复盘'
  };
  var EVENT_LABEL = {
    created: '登记发布实验',
    published: '登记发布',
    measured: '回填实测',
    reviewed: '完成复盘',
    legacy_imported: '导入旧记录',
    legacy_reviewed: '导入旧复盘'
  };
  var currentExperiments = [];

  function el(id) { return document.getElementById(id); }
  function make(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }
  function text(node, value) {
    node.textContent = value == null ? '' : String(value);
    return node;
  }
  function appendTextElement(parent, tag, className, value) {
    var node = make(tag, className);
    text(node, value);
    parent.appendChild(node);
    return node;
  }
  function metricFor(key) {
    return METRIC_BY_KEY[key] || { key: key, label: key, unit: '', integer: false };
  }
  function formatNumber(value) {
    var number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(number);
  }
  function formatMetricValue(key, value) {
    var metric = metricFor(key);
    return formatNumber(value) + (metric.unit ? ' ' + metric.unit : '');
  }
  function formatDateTime(value) {
    if (!value) return '—';
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(date);
  }
  function toDateTimeLocal(value) {
    var date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) date = new Date();
    var local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }
  function localInputToIso(value) {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return date.toISOString();
  }
  function validHttpUrl(value) {
    try {
      var parsed = new URL(value);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch (_error) {
      return false;
    }
  }
  function safeLink(label, href) {
    if (!href || !validHttpUrl(href)) return text(make('span', 'meta-value'), '—');
    var link = make('a', 'publish-link');
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    text(link, label || href);
    return link;
  }

  function humaniseLocation(location) {
    if (!Array.isArray(location)) return '';
    return location.filter(function (item) { return item !== 'body'; }).join('.');
  }
  function formatErrorDetail(detail) {
    if (!detail) return '';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map(function (item) {
        if (item && typeof item === 'object') {
          var location = humaniseLocation(item.loc);
          var message = item.msg || item.message || JSON.stringify(item);
          return (location ? location + '：' : '') + message;
        }
        return String(item);
      }).join('；');
    }
    if (typeof detail === 'object') {
      return detail.message || detail.msg || JSON.stringify(detail);
    }
    return String(detail);
  }

  function api(path, options) {
    return fetch(path, options).then(function (response) {
      return response.text().then(function (body) {
        var data = null;
        if (body) {
          try { data = JSON.parse(body); } catch (_error) { data = { detail: body }; }
        }
        if (!response.ok) {
          var message = formatErrorDetail(data && data.detail) || ('HTTP ' + response.status);
          var error = new Error(message);
          error.status = response.status;
          error.payload = data;
          throw error;
        }
        return data || {};
      });
    });
  }

  function setMessage(node, message, kind) {
    if (!node) return;
    text(node, message || '');
    node.className = 'publish-message' + (kind ? ' publish-message--' + kind : '');
  }
  function setPending(button, pending, pendingLabel) {
    if (!button) return;
    if (pending) {
      button.dataset.idleLabel = button.textContent;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      text(button, pendingLabel || '处理中…');
    } else {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      if (button.dataset.idleLabel) text(button, button.dataset.idleLabel);
      delete button.dataset.idleLabel;
    }
  }
  function markValidity(input, valid) {
    if (!input) return;
    if (valid) input.removeAttribute('aria-invalid');
    else input.setAttribute('aria-invalid', 'true');
  }

  function renderScores() {
    var grid = el('scoreGrid');
    if (!grid) return;
    grid.textContent = '';
    SCORE_DIMENSIONS.forEach(function (dimension, index) {
      var item = make('div', 'score-item');
      item.dataset.dimension = dimension;
      var label = make('label');
      label.htmlFor = 'score_' + index;
      text(label, dimension);
      var input = make('input');
      input.id = 'score_' + index;
      input.type = 'number';
      input.min = '1';
      input.max = '5';
      input.step = '1';
      input.inputMode = 'numeric';
      item.appendChild(label);
      item.appendChild(input);
      grid.appendChild(item);
    });
  }

  function renderPredictionInputs() {
    var grid = el('predGrid');
    if (!grid) return;
    grid.textContent = '';
    METRICS.forEach(function (metric) {
      var item = make('div', 'prediction-item');
      item.dataset.metricKey = metric.key;
      var heading = make('div', 'prediction-item__heading');
      appendTextElement(heading, 'strong', '', metric.label);
      appendTextElement(heading, 'span', '', metric.unit);
      item.appendChild(heading);
      var bounds = make('div', 'prediction-item__bounds');
      ['low', 'high'].forEach(function (bound) {
        var wrap = make('div');
        var inputId = 'prediction_' + metric.key + '_' + bound;
        var label = make('label');
        label.htmlFor = inputId;
        text(label, bound === 'low' ? '最低预期' : '最高预期');
        var input = make('input');
        input.id = inputId;
        input.dataset.bound = bound;
        input.type = 'number';
        input.min = '0';
        input.step = metric.integer ? '1' : '0.1';
        if (metric.max != null) input.max = String(metric.max);
        input.inputMode = 'decimal';
        wrap.appendChild(label);
        wrap.appendChild(input);
        bounds.appendChild(wrap);
      });
      item.appendChild(bounds);
      grid.appendChild(item);
    });
  }

  function collectCreatePayload() {
    var errors = [];
    var titleInput = el('cTitle');
    var platformInput = el('cPlatform');
    var sourceInput = el('cSourceUrl');
    var titleValue = titleInput.value.trim();
    var platformValue = platformInput.value.trim();
    var sourceValue = sourceInput.value.trim();
    var predictionWindowInput = el('cWindowHours');
    var predictionWindowRaw = predictionWindowInput.value.trim();
    var predictionWindow = predictionWindowRaw ? Number(predictionWindowRaw) : 72;
    markValidity(titleInput, Boolean(titleValue));
    markValidity(platformInput, Boolean(platformValue));
    markValidity(sourceInput, !sourceValue || validHttpUrl(sourceValue));
    if (!titleValue) errors.push('请填写标题');
    if (!platformValue) errors.push('请填写计划发布平台');
    if (sourceValue && !validHttpUrl(sourceValue)) errors.push('来源链接必须以 http:// 或 https:// 开头');
    var windowValid = Number.isInteger(predictionWindow) && predictionWindow >= 1 && predictionWindow <= 8760;
    markValidity(predictionWindowInput, windowValid);
    if (!windowValid) errors.push('观察窗口必须是 1–8760 的整数小时');

    var scores = [];
    Array.prototype.forEach.call(document.querySelectorAll('#scoreGrid .score-item'), function (item) {
      var input = item.querySelector('input');
      var raw = input.value.trim();
      if (!raw) {
        markValidity(input, true);
        return;
      }
      var score = Number(raw);
      var valid = Number.isInteger(score) && score >= 1 && score <= 5;
      markValidity(input, valid);
      if (!valid) errors.push(item.dataset.dimension + '必须是 1–5 的整数');
      else scores.push({ dimension: item.dataset.dimension, score: score });
    });

    var predictions = [];
    Array.prototype.forEach.call(document.querySelectorAll('#predGrid .prediction-item'), function (item) {
      var metric = metricFor(item.dataset.metricKey);
      var lowInput = item.querySelector('[data-bound="low"]');
      var highInput = item.querySelector('[data-bound="high"]');
      var lowRaw = lowInput.value.trim();
      var highRaw = highInput.value.trim();
      if (!lowRaw && !highRaw) {
        markValidity(lowInput, true);
        markValidity(highInput, true);
        return;
      }
      if (!lowRaw || !highRaw) {
        markValidity(lowInput, Boolean(lowRaw));
        markValidity(highInput, Boolean(highRaw));
        errors.push(metric.label + '必须同时填写最低和最高预期');
        return;
      }
      var low = Number(lowRaw);
      var high = Number(highRaw);
      var numberValid = Number.isFinite(low) && Number.isFinite(high) && low >= 0 && high >= 0;
      var integerValid = !metric.integer || (Number.isInteger(low) && Number.isInteger(high));
      var maxValid = metric.max == null || (low <= metric.max && high <= metric.max);
      var orderValid = high >= low;
      var valid = numberValid && integerValid && maxValid && orderValid;
      markValidity(lowInput, valid);
      markValidity(highInput, valid);
      if (!numberValid) errors.push(metric.label + '必须是非负数字');
      else if (!integerValid) errors.push(metric.label + '是计数指标，只能填写整数');
      else if (!maxValid) errors.push(metric.label + '必须在 0–100%');
      else if (!orderValid) errors.push(metric.label + '的最高预期不能小于最低预期');
      else predictions.push({ key: metric.key, low: low, high: high });
    });
    return {
      errors: errors,
      payload: {
        title: titleValue,
        source_topic_id: el('cSourceTopicId').value.trim() || null,
        source_url: sourceValue || null,
        analysis_ref: el('cAnalysisRef').value.trim() || null,
        content_summary: el('cContentSummary').value.trim() || null,
        platform: platformValue,
        hypothesis: el('cHypothesis').value.trim() || null,
        window_hours: predictionWindow,
        scores: scores,
        predictions: predictions
      }
    };
  }

  function clearCreateForm() {
    el('createForm').reset();
    renderScores();
    renderPredictionInputs();
  }

  function discardDraft() {
    try { window.sessionStorage.removeItem('project024_publish_draft'); } catch (_error) { /* 无可用会话存储时忽略 */ }
  }

  function createExperiment(event) {
    event.preventDefault();
    var button = el('createSubmit');
    var message = el('createMsg');
    var result = collectCreatePayload();
    if (result.errors.length) {
      setMessage(message, result.errors.join('；') + '。', 'error');
      return;
    }
    setPending(button, true, '正在登记…');
    setMessage(message, '正在登记并生成内容版本哈希…', 'info');
    api('/api/publish/experiments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result.payload)
    }).then(function () {
      setMessage(message, '登记成功。下一步可登记实际发布信息。', 'success');
      discardDraft();
      clearCreateForm();
      return Promise.all([loadExperiments(), loadCalibrationSummary()]);
    }).catch(function (error) {
      setMessage(message, '登记失败：' + error.message, 'error');
    }).finally(function () {
      setPending(button, false);
    });
  }

  function renderCalibrationSummary(summary) {
    var container = el('calibrationSummary');
    var state = el('calibrationState');
    container.textContent = '';
    var sampleSize = Number(summary.sample_size || 0);
    var observations = Number(summary.metric_observations || 0);
    var hitRate = summary.hit_rate == null ? null : Number(summary.hit_rate);
    var insufficient = summary.evidence_insufficient !== false;
    text(state, insufficient ? '证据不足' : '已有参考样本');
    state.className = 'calibration-state ' + (insufficient ? 'calibration-state--warning' : 'calibration-state--ready');
    var stats = make('dl', 'calibration-stats');
    [
      ['有基线复盘', sampleSize + ' 次'],
      ['已对照指标', observations + ' 项'],
      ['基线命中率', hitRate == null ? '暂无' : formatNumber(hitRate * 100) + '%']
    ].forEach(function (item) {
      var wrap = make('div');
      appendTextElement(wrap, 'dt', '', item[0]);
      appendTextElement(wrap, 'dd', '', item[1]);
      stats.appendChild(wrap);
    });
    container.appendChild(stats);
    appendTextElement(
      container,
      'p',
      'calibration-note' + (insufficient ? ' calibration-note--warning' : ''),
      summary.message || (insufficient ? '样本不足，暂不能概括稳定规律。' : '命中率只用于校准预测，不代表因果关系。')
    );
  }

  function loadCalibrationSummary() {
    return api('/api/publish/calibration-summary').then(function (data) {
      renderCalibrationSummary(data.summary || data);
    }).catch(function (error) {
      var container = el('calibrationSummary');
      container.textContent = '';
      appendTextElement(container, 'p', 'publish-empty publish-empty--error', '校准摘要加载失败：' + error.message);
      var state = el('calibrationState');
      text(state, '暂不可用');
      state.className = 'calibration-state calibration-state--warning';
    });
  }

  function addMetaRow(list, label, valueNodeOrText) {
    var row = make('div', 'experiment-meta__row');
    appendTextElement(row, 'dt', '', label);
    var dd = make('dd');
    if (valueNodeOrText && valueNodeOrText.nodeType) dd.appendChild(valueNodeOrText);
    else text(dd, valueNodeOrText == null || valueNodeOrText === '' ? '—' : valueNodeOrText);
    row.appendChild(dd);
    list.appendChild(row);
  }

  function renderScoreChips(parent, scores) {
    if (!scores || !scores.length) return;
    var section = make('section', 'experiment-section');
    appendTextElement(section, 'h3', '', '发布前自评');
    var chips = make('div', 'experiment-chips');
    (scores || []).forEach(function (score) {
      appendTextElement(chips, 'span', 'experiment-chip', score.dimension + ' ' + score.score + '/5');
    });
    section.appendChild(chips);
    parent.appendChild(section);
  }

  function createTable(headers) {
    var wrapper = make('div', 'publish-table-wrap');
    wrapper.tabIndex = 0;
    var table = make('table', 'publish-table');
    var head = make('thead');
    var row = make('tr');
    headers.forEach(function (header) { appendTextElement(row, 'th', '', header); });
    head.appendChild(row);
    table.appendChild(head);
    table.appendChild(make('tbody'));
    wrapper.appendChild(table);
    return { wrapper: wrapper, body: table.querySelector('tbody') };
  }

  function appendTableRow(body, values) {
    var row = make('tr');
    values.forEach(function (value) { appendTextElement(row, 'td', '', value); });
    body.appendChild(row);
  }

  function renderPredictions(parent, predictions) {
    var section = make('section', 'experiment-section');
    appendTextElement(section, 'h3', '', '发布前复盘基线');
    if (!predictions || !predictions.length) {
      appendTextElement(section, 'p', 'experiment-section__note', '本次未填写指标基线；仍可回填真实数据，但不会计入基线命中率或偏差统计。');
      parent.appendChild(section);
      return;
    }
    var table = createTable(['指标', '预期下限', '预期上限', '单位']);
    (predictions || []).forEach(function (prediction) {
      var metric = metricFor(prediction.key);
      appendTableRow(table.body, [metric.label, formatNumber(prediction.low), formatNumber(prediction.high), metric.unit]);
    });
    section.appendChild(table.wrapper);
    parent.appendChild(section);
  }

  function renderActualMetrics(parent, record) {
    var metrics = record.actual_metrics || {};
    if (!Object.keys(metrics).length) return;
    var section = make('section', 'experiment-section');
    appendTextElement(section, 'h3', '', '实测数据');
    appendTextElement(section, 'p', 'experiment-section__note', '观察窗：T+' + record.window_hours + ' 小时 · 观测时间：' + formatDateTime(record.observed_at) + ' · 数据来源：' + (record.data_source || '—'));
    if (record.backfill_note) appendTextElement(section, 'p', 'experiment-section__note', '回填备注：' + record.backfill_note);
    var table = createTable(['指标', '实测值', '单位']);
    Object.keys(metrics).forEach(function (key) {
      var metric = metricFor(key);
      appendTableRow(table.body, [metric.label, formatNumber(metrics[key]), metric.unit]);
    });
    section.appendChild(table.wrapper);
    parent.appendChild(section);
  }

  function renderReview(parent, record) {
    var section = make('section', 'experiment-section');
    if (!record.deviations || !record.deviations.length) {
      if (record.status !== 'reviewed') return;
      appendTextElement(section, 'h3', '', '发布复盘');
      appendTextElement(section, 'p', 'experiment-section__note', record.learning_note || '本次没有可比较的发布前基线；真实数据已保存，不计入命中率或偏差统计。');
      if (record.calibration_summary) {
        appendTextElement(section, 'p', 'experiment-section__note', '跨实验基线摘要：' + (record.calibration_summary.message || '暂无说明'));
      }
      parent.appendChild(section);
      return;
    }
    appendTextElement(section, 'h3', '', '基线对照复盘');
    var table = createTable(['指标', '预测区间', '实际', '判断', '偏差']);
    record.deviations.forEach(function (deviation) {
      appendTableRow(table.body, [
        deviation.label || metricFor(deviation.key).label,
        formatMetricValue(deviation.key, deviation.predicted_low) + ' – ' + formatMetricValue(deviation.key, deviation.predicted_high),
        formatMetricValue(deviation.key, deviation.actual),
        deviation.inside_interval ? '命中区间' : (deviation.note || '偏离区间'),
        formatMetricValue(deviation.key, deviation.error)
      ]);
    });
    section.appendChild(table.wrapper);
    if (record.next_suggestions && record.next_suggestions.length) {
      appendTextElement(section, 'h4', 'experiment-subheading', '下一轮可验证动作');
      var list = make('ul', 'suggestion-list');
      record.next_suggestions.forEach(function (suggestion) {
        appendTextElement(list, 'li', '', '[' + suggestion.target + '] ' + suggestion.direction + '：' + suggestion.rationale);
      });
      section.appendChild(list);
    }
    if (record.learning_candidate) {
      appendTextElement(section, 'p', 'learning-candidate', '候选经验（需人工确认）：' + (record.learning_note || '等待补充说明'));
    }
    if (record.calibration_summary) {
      appendTextElement(section, 'p', 'experiment-section__note', '跨实验基线摘要：' + (record.calibration_summary.message || '暂无说明'));
    }
    parent.appendChild(section);
  }

  function actionForStatus(record) {
    if (record.status === 'predicted') return { key: 'publish', label: '登记发布' };
    if (record.status === 'published') return { key: 'backfill', label: '回填真实数据' };
    if (record.status === 'measured') return {
      key: 'review',
      label: record.predictions && record.predictions.length ? '对照基线复盘' : '完成发布复盘'
    };
    return null;
  }

  function renderActions(parent, record) {
    var section = make('section', 'experiment-actions');
    var action = actionForStatus(record);
    if (action) {
      var button = make('button', 'button button--secondary');
      button.type = 'button';
      button.dataset.action = action.key;
      button.dataset.id = record.id;
      text(button, action.label);
      section.appendChild(button);
      appendTextElement(section, 'span', 'experiment-actions__hint', '只开放当前状态允许的下一步。');
    } else {
      appendTextElement(section, 'span', 'experiment-actions__complete', '本轮已复盘，可登记下一次内容实验继续积累。');
    }
    var eventsButton = make('button', 'button button--secondary');
    eventsButton.type = 'button';
    eventsButton.dataset.events = 'true';
    eventsButton.dataset.id = record.id;
    text(eventsButton, '查看事件历史');
    section.appendChild(eventsButton);
    parent.appendChild(section);

    var formHost = make('div', 'inline-form-host');
    formHost.dataset.formFor = record.id;
    formHost.hidden = true;
    parent.appendChild(formHost);
    var eventHost = make('div', 'event-history');
    eventHost.dataset.eventsFor = record.id;
    eventHost.hidden = true;
    parent.appendChild(eventHost);
  }

  function experimentCard(record) {
    var article = make('article', 'experiment-card');
    article.dataset.experimentId = record.id;
    var header = make('header', 'experiment-card__header');
    var titleWrap = make('div');
    var badge = appendTextElement(titleWrap, 'span', 'experiment-badge experiment-badge--' + record.status, STATUS_LABEL[record.status] || record.status);
    badge.dataset.status = record.status;
    appendTextElement(titleWrap, 'h2', 'experiment-title', record.title || '未命名实验');
    header.appendChild(titleWrap);
    appendTextElement(header, 'span', 'experiment-id', '实验 ' + record.id);
    article.appendChild(header);

    if (record.hypothesis) appendTextElement(article, 'p', 'experiment-hypothesis', '发布前假设：' + record.hypothesis);
    var meta = make('dl', 'experiment-meta');
    addMetaRow(meta, '平台', record.platform);
    addMetaRow(meta, '登记时间', formatDateTime(record.created_at));
    addMetaRow(meta, '分析引用', record.analysis_ref || '—');
    addMetaRow(meta, '抖音内容编号', record.source_topic_id || '—');
    var hash = make('code', 'snapshot-hash');
    text(hash, record.content_snapshot_sha256 || '—');
    addMetaRow(meta, '内容版本哈希', hash);
    addMetaRow(meta, '来源链接', record.source_url ? safeLink('打开来源内容', record.source_url) : '—');
    addMetaRow(meta, '发布链接', record.publish_url ? safeLink('打开已发布内容', record.publish_url) : '—');
    addMetaRow(meta, '发布时间', formatDateTime(record.published_at));
    addMetaRow(meta, '数据观察窗口', 'T+' + (record.prediction_window_hours || 72) + ' 小时');
    if (record.window_hours) addMetaRow(meta, '观察窗口', 'T+' + record.window_hours + ' 小时');
    article.appendChild(meta);
    if (record.content_summary) appendTextElement(article, 'p', 'content-snapshot', '内容快照：' + record.content_summary);

    renderScoreChips(article, record.scores);
    renderPredictions(article, record.predictions);
    renderActualMetrics(article, record);
    renderReview(article, record);
    renderActions(article, record);
    return article;
  }

  function findRecord(id) {
    return currentExperiments.find(function (record) { return String(record.id) === String(id); });
  }
  function field(labelText, input, required) {
    var wrap = make('div', 'publish-field');
    var label = make('label');
    if (input.id) label.htmlFor = input.id;
    text(label, labelText + (required ? ' *' : ''));
    wrap.appendChild(label);
    wrap.appendChild(input);
    return wrap;
  }
  function inputFor(name, id, type) {
    var input = make('input');
    input.name = name;
    input.id = id;
    input.type = type || 'text';
    return input;
  }

  function buildPublishForm(record, host) {
    var form = make('form', 'inline-action-form');
    form.dataset.inlineAction = 'publish';
    form.dataset.id = record.id;
    appendTextElement(form, 'h3', '', '登记实际发布信息');
    appendTextElement(form, 'p', 'inline-action-form__help', '平台、发布时间和链接会写入当前实验；提交后按既定顺序进入数据回填。');
    var grid = make('div', 'publish-form-grid publish-form-grid--three');
    var platform = inputFor('platform', 'publish_platform_' + record.id);
    platform.required = true;
    platform.maxLength = 200;
    platform.value = record.platform || '';
    grid.appendChild(field('实际发布平台', platform, true));
    var publishedAt = inputFor('published_at', 'published_at_' + record.id, 'datetime-local');
    publishedAt.required = true;
    publishedAt.value = toDateTimeLocal();
    grid.appendChild(field('实际发布时间', publishedAt, true));
    var url = inputFor('publish_url', 'publish_url_' + record.id, 'url');
    url.required = true;
    url.maxLength = 2048;
    url.placeholder = 'https://...';
    grid.appendChild(field('实际发布链接', url, true));
    form.appendChild(grid);
    finishInlineForm(form, '确认登记发布');
    host.appendChild(form);
  }

  function buildBackfillForm(record, host) {
    var form = make('form', 'inline-action-form');
    form.dataset.inlineAction = 'backfill';
    form.dataset.id = record.id;
    appendTextElement(form, 'h3', '', '回填同口径实测数据');
    appendTextElement(form, 'p', 'inline-action-form__help', '可填写创作者中心当前可见的任意指标；只对登记时写过基线的指标计算命中或偏差。至少回填一项，不要混用不同观察时点的数据。');
    var metricGrid = make('div', 'backfill-metric-grid');
    var predictedKeys = (record.predictions || []).reduce(function (keys, prediction) {
      keys[prediction.key] = true;
      return keys;
    }, {});
    METRICS.forEach(function (metric) {
      var input = inputFor('metric_' + metric.key, 'actual_' + record.id + '_' + metric.key, 'number');
      input.dataset.metricKey = metric.key;
      input.min = '0';
      input.step = metric.integer ? '1' : '0.1';
      if (metric.max != null) input.max = String(metric.max);
      input.inputMode = 'decimal';
      input.placeholder = metric.unit;
      var baselineLabel = predictedKeys[metric.key] ? '，已设基线' : '';
      metricGrid.appendChild(field(metric.label + '（' + metric.unit + baselineLabel + '）', input, false));
    });
    form.appendChild(metricGrid);
    var detailGrid = make('div', 'publish-form-grid publish-form-grid--three');
    var windowHours = inputFor('window_hours', 'window_' + record.id, 'number');
    windowHours.required = true;
    windowHours.min = '1';
    windowHours.max = '8760';
    windowHours.step = '1';
    windowHours.value = String(record.prediction_window_hours || 72);
    windowHours.readOnly = true;
    detailGrid.appendChild(field('观察窗口（小时）', windowHours, true));
    var observedAt = inputFor('observed_at', 'observed_' + record.id, 'datetime-local');
    observedAt.required = true;
    observedAt.value = toDateTimeLocal();
    detailGrid.appendChild(field('数据观测时间', observedAt, true));
    var dataSource = inputFor('data_source', 'source_' + record.id);
    dataSource.required = true;
    dataSource.maxLength = 300;
    dataSource.value = '平台后台人工读取';
    detailGrid.appendChild(field('数据来源', dataSource, true));
    form.appendChild(detailGrid);
    var note = make('textarea');
    note.name = 'note';
    note.id = 'backfill_note_' + record.id;
    note.maxLength = 5000;
    note.rows = 2;
    note.placeholder = '可记录截图位置、口径差异或异常流量。';
    form.appendChild(field('回填备注', note, false));
    finishInlineForm(form, '确认回填实测');
    host.appendChild(form);
  }

  function buildReviewForm(record, host) {
    var form = make('form', 'inline-action-form');
    form.dataset.inlineAction = 'review';
    form.dataset.id = record.id;
    var hasBaseline = record.predictions && record.predictions.length;
    appendTextElement(form, 'h3', '', hasBaseline ? '对照发布前基线' : '完成发布复盘');
    appendTextElement(form, 'p', 'inline-action-form__help', hasBaseline
      ? '系统只对同时具有基线和实测值的指标判断区间是否命中；不会把单次数据写成因果结论。'
      : '本次没有指标基线，系统只保存真实结果和人工备注，不计算命中率或偏差。');
    var note = make('textarea');
    note.name = 'note';
    note.id = 'review_note_' + record.id;
    note.maxLength = 5000;
    note.rows = 3;
    note.placeholder = '可补充本轮已知的异常情况或人工判断。';
    form.appendChild(field('人工复盘备注', note, false));
    finishInlineForm(form, '确认生成复盘');
    host.appendChild(form);
  }

  function finishInlineForm(form, buttonLabel) {
    var footer = make('div', 'inline-action-form__footer');
    var submit = make('button', 'button button--primary');
    submit.type = 'submit';
    text(submit, buttonLabel);
    footer.appendChild(submit);
    var cancel = make('button', 'button button--secondary');
    cancel.type = 'button';
    cancel.dataset.cancelInline = 'true';
    text(cancel, '取消');
    footer.appendChild(cancel);
    var message = make('p', 'publish-message');
    message.dataset.inlineMessage = 'true';
    message.setAttribute('role', 'status');
    message.setAttribute('aria-live', 'polite');
    footer.appendChild(message);
    form.appendChild(footer);
    form.addEventListener('submit', submitInlineAction);
  }

  function openInlineForm(record, action) {
    var host = document.querySelector('[data-form-for="' + record.id + '"]');
    if (!host) return;
    host.textContent = '';
    host.hidden = false;
    if (action === 'publish') buildPublishForm(record, host);
    else if (action === 'backfill') buildBackfillForm(record, host);
    else if (action === 'review') buildReviewForm(record, host);
    var firstInput = host.querySelector('input, textarea');
    if (firstInput) firstInput.focus();
  }

  function collectInlineAction(form) {
    var action = form.dataset.inlineAction;
    var errors = [];
    var payload = {};
    if (action === 'publish') {
      var platform = form.elements.platform.value.trim();
      var publishedAtRaw = form.elements.published_at.value;
      var publishUrl = form.elements.publish_url.value.trim();
      var publishedAt = localInputToIso(publishedAtRaw);
      if (!platform) errors.push('请填写实际发布平台');
      if (!publishedAt) errors.push('请填写有效的发布时间');
      if (!validHttpUrl(publishUrl)) errors.push('发布链接必须以 http:// 或 https:// 开头');
      payload = { platform: platform, published_at: publishedAt, publish_url: publishUrl };
    } else if (action === 'backfill') {
      var metrics = {};
      Array.prototype.forEach.call(form.querySelectorAll('[data-metric-key]'), function (input) {
        var raw = input.value.trim();
        if (!raw) return;
        var metric = metricFor(input.dataset.metricKey);
        var value = Number(raw);
        if (!Number.isFinite(value) || value < 0) errors.push(metric.label + '必须是非负数字');
        else if (metric.integer && !Number.isInteger(value)) errors.push(metric.label + '只能填写整数');
        else if (metric.max != null && value > metric.max) errors.push(metric.label + '必须在 0–100%');
        else metrics[metric.key] = value;
      });
      if (!Object.keys(metrics).length) errors.push('请至少回填一个真实指标');
      var windowHours = Number(form.elements.window_hours.value);
      if (!Number.isInteger(windowHours) || windowHours < 1 || windowHours > 8760) errors.push('观察窗口必须是 1–8760 的整数小时');
      var observedAt = localInputToIso(form.elements.observed_at.value);
      if (!observedAt) errors.push('请填写有效的数据观测时间');
      var dataSource = form.elements.data_source.value.trim();
      if (!dataSource) errors.push('请填写数据来源');
      payload = {
        metrics: metrics,
        window_hours: windowHours,
        observed_at: observedAt,
        data_source: dataSource,
        note: form.elements.note.value.trim() || null
      };
    } else if (action === 'review') {
      payload = { note: form.elements.note.value.trim() || null };
    }
    return { errors: errors, payload: payload };
  }

  function submitInlineAction(event) {
    event.preventDefault();
    var form = event.currentTarget;
    var action = form.dataset.inlineAction;
    var id = form.dataset.id;
    var message = form.querySelector('[data-inline-message]');
    var button = form.querySelector('button[type="submit"]');
    var result = collectInlineAction(form);
    if (result.errors.length) {
      setMessage(message, result.errors.join('；') + '。', 'error');
      return;
    }
    var route = '/api/publish/experiments/' + encodeURIComponent(id) + '/' + action;
    setPending(button, true, '正在提交…');
    setMessage(message, '正在保存本次状态变化…', 'info');
    api(route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result.payload)
    }).then(function () {
      return Promise.all([loadExperiments(), loadCalibrationSummary()]);
    }).catch(function (error) {
      setMessage(message, '操作失败：' + error.message, 'error');
      setPending(button, false);
    });
  }

  function renderEvents(host, events) {
    host.textContent = '';
    host.hidden = false;
    appendTextElement(host, 'h3', '', '不可变事件历史');
    if (!events.length) {
      appendTextElement(host, 'p', 'publish-empty', '暂无事件记录。');
      return;
    }
    var list = make('ol', 'event-list');
    events.forEach(function (event) {
      var item = make('li');
      appendTextElement(item, 'strong', '', EVENT_LABEL[event.event_type] || event.event_type);
      appendTextElement(item, 'span', '', formatDateTime(event.occurred_at) + ' · 版本 ' + event.record_version);
      list.appendChild(item);
    });
    host.appendChild(list);
  }

  function loadEvents(id, button) {
    var host = document.querySelector('[data-events-for="' + id + '"]');
    if (!host) return;
    if (host.dataset.loaded === 'true') {
      host.hidden = !host.hidden;
      text(button, host.hidden ? '查看事件历史' : '收起事件历史');
      return;
    }
    setPending(button, true, '读取历史…');
    api('/api/publish/experiments/' + encodeURIComponent(id) + '/events').then(function (data) {
      var events = Array.isArray(data) ? data : (data.events || []);
      renderEvents(host, events);
      host.dataset.loaded = 'true';
      setPending(button, false);
      text(button, '收起事件历史');
    }).catch(function (error) {
      host.textContent = '';
      host.hidden = false;
      appendTextElement(host, 'p', 'publish-empty publish-empty--error', '事件历史加载失败：' + error.message);
      setPending(button, false);
    });
  }

  function loadExperiments() {
    var container = el('experiments');
    return api('/api/publish/experiments').then(function (data) {
      var items = Array.isArray(data) ? data : (data.experiments || []);
      currentExperiments = items;
      container.textContent = '';
      if (!items.length) {
        appendTextElement(container, 'p', 'publish-empty', '还没有发布实验。请先在上方登记本轮内容实验。');
        return;
      }
      items.forEach(function (record) { container.appendChild(experimentCard(record)); });
    }).catch(function (error) {
      container.textContent = '';
      appendTextElement(container, 'p', 'publish-empty publish-empty--error', '实验列表加载失败：' + error.message);
    });
  }

  function onExperimentsClick(event) {
    var button = event.target.closest('button');
    if (!button) return;
    var id = button.dataset.id;
    if (button.dataset.cancelInline) {
      var host = button.closest('.inline-form-host');
      if (host) { host.textContent = ''; host.hidden = true; }
      return;
    }
    if (button.dataset.action && id) {
      var record = findRecord(id);
      if (record) openInlineForm(record, button.dataset.action);
      return;
    }
    if (button.dataset.events && id) loadEvents(id, button);
  }

  function readPublishDraft() {
    var raw;
    try { raw = window.sessionStorage.getItem('project024_publish_draft'); } catch (_error) { return null; }
    if (!raw) return null;
    try {
      var source = JSON.parse(raw);
      if (!source || typeof source !== 'object' || Array.isArray(source)) return null;
      return {
        title: typeof source.title === 'string' ? source.title : '',
        source_topic_id: typeof source.source_topic_id === 'string' ? source.source_topic_id : (typeof source.sourceTopicId === 'string' ? source.sourceTopicId : ''),
        platform: typeof source.platform === 'string' ? source.platform : '',
        source_url: typeof source.source_url === 'string' ? source.source_url : (typeof source.sourceUrl === 'string' ? source.sourceUrl : ''),
        analysis_ref: typeof source.analysis_ref === 'string' ? source.analysis_ref : (typeof source.analysisRef === 'string' ? source.analysisRef : ''),
        content_summary: typeof source.content_summary === 'string' ? source.content_summary : (typeof source.contentSummary === 'string' ? source.contentSummary : ''),
        hypothesis: typeof source.hypothesis === 'string' ? source.hypothesis : '',
        window_hours: Number(source.window_hours || source.windowHours || 72),
        scores: Array.isArray(source.scores) ? source.scores : [],
        predictions: Array.isArray(source.predictions) ? source.predictions : []
      };
    } catch (_error) {
      return null;
    }
  }

  function prefillFromDraft() {
    var draft = readPublishDraft();
    if (!draft) return;
    [['cTitle', draft.title], ['cPlatform', draft.platform], ['cSourceTopicId', draft.source_topic_id], ['cSourceUrl', draft.source_url], ['cAnalysisRef', draft.analysis_ref], ['cContentSummary', draft.content_summary], ['cHypothesis', draft.hypothesis]].forEach(function (pair) {
      if (pair[1] && !el(pair[0]).value) el(pair[0]).value = pair[1];
    });
    if (Number.isInteger(draft.window_hours) && draft.window_hours >= 1 && draft.window_hours <= 8760) {
      el('cWindowHours').value = String(draft.window_hours);
    }
    draft.scores.forEach(function (score) {
      if (!score || !SCORE_DIMENSIONS.includes(score.dimension)) return;
      var value = Number(score.score);
      if (!Number.isInteger(value) || value < 1 || value > 5) return;
      var item = Array.prototype.find.call(document.querySelectorAll('.score-item'), function (node) { return node.dataset.dimension === score.dimension; });
      if (item) item.querySelector('input').value = String(value);
    });
    if (draft.scores.length) el('advancedScores').open = true;
    draft.predictions.forEach(function (prediction) {
      if (!prediction || !METRIC_BY_KEY[prediction.key]) return;
      var item = document.querySelector('[data-metric-key="' + prediction.key + '"]');
      if (!item) return;
      if (Number.isFinite(Number(prediction.low))) item.querySelector('[data-bound="low"]').value = String(prediction.low);
      if (Number.isFinite(Number(prediction.high))) item.querySelector('[data-bound="high"]').value = String(prediction.high);
    });
    if (draft.predictions.length) el('advancedPredictions').open = true;
    setMessage(el('createMsg'), '已从本次内容分析预填用户可见信息；请核对后登记。', 'info');
  }

  window.project024AgentBridge = {
    getContext: function (mode) {
      return {
        draft: mode === 'script' ? el('cContentSummary').value : el('cHypothesis').value,
        context: {
          title: el('cTitle').value,
          platform: el('cPlatform').value,
          source_topic_id: el('cSourceTopicId').value,
          source_url: el('cSourceUrl').value,
          observation_window_hours: Number(el('cWindowHours').value || 72),
          registered_experiments: currentExperiments.slice(0, 5).map(function (item) {
            return {
              id: item.id,
              title: item.title,
              status: item.status,
              hypothesis: item.hypothesis,
              actual_metrics: item.actual_metrics
            };
          })
        }
      };
    },
  applyDraft: function (mode, value) {
    var input = mode === 'script' ? el('cContentSummary') : el('cHypothesis');
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    setMessage(el('createMsg'), mode === 'script' ? 'Agent 脚本已应用到内容快照。' : 'Agent 策略已应用到本轮实验假设。', 'success');
    return {
      persisted: false,
      message: '已写回发布实验表单；提交登记后才会保存。'
    };
  }
};

  function init() {
    renderScores();
    renderPredictionInputs();
    prefillFromDraft();
    el('createForm').addEventListener('submit', createExperiment);
    el('experiments').addEventListener('click', onExperimentsClick);
    el('reloadExperiments').addEventListener('click', function (event) {
      var button = event.currentTarget;
      setPending(button, true, '刷新中…');
      Promise.all([loadExperiments(), loadCalibrationSummary()]).finally(function () { setPending(button, false); });
    });
    Promise.all([loadExperiments(), loadCalibrationSummary()]).finally(function () {
      document.body.dataset.publishReady = 'true';
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
