const form = document.querySelector('#job-form');
const jobsEl = document.querySelector('#jobs');
const errorEl = document.querySelector('#form-error');
const workerEl = document.querySelector('#worker-status');
const strategyEl = document.querySelector('#strategy-report');
const discoveryButton = document.querySelector('#discovery-run');
const discoveryStatusEl = document.querySelector('#discovery-status');
let mode = 'link';

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value ?? '';
  return div.innerHTML;
}

function statusLabel(job) {
  return job.status === 'done' ? 'Pronto' : job.status === 'failed' ? 'Falhou' : job.status === 'queued' ? 'Na fila' : job.stage;
}

function publicationLabel(item) {
  if (!item) return '';
  if (item.publish_at) {
    const date = new Date(item.publish_at);
    const label = new Intl.DateTimeFormat('pt-BR', {
      timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    }).format(date);
    return `Agendado · ${label}`;
  }
  return item.status === 'published' ? 'Publicado no YouTube' : '';
}

function qualityLabel(quality) {
  if (!quality || typeof quality.total !== 'number') return 'modelo anterior';
  const total = Number(quality.total).toFixed(1);
  return `qualidade ${total}/10 · ${quality.duration_band || 'padrão'}`;
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsEl.innerHTML = '<div class="empty"><div class="empty-mark">✂</div><p>Seu primeiro vídeo aparecerá aqui.</p></div>';
    return;
  }
  jobsEl.innerHTML = jobs.map((job, index) => {
    const publications = new Map((job.publication?.items || []).map(item => [item.file, item]));
    const clips = (job.outputs || []).map(clip => {
      const publication = publications.get(clip.file);
      const youtube = publication ? `<a href="${escapeHtml(publication.url)}" target="_blank" rel="noopener">${publicationLabel(publication)}</a>` : '';
      const alternatives = (clip.title_options || []).slice(1).map(title => `<li>${escapeHtml(title)}</li>`).join('');
      const warnings = (clip.quality?.flags || []).map(flag => `<span class="quality-flag">${escapeHtml(flag.replaceAll('_', ' '))}</span>`).join('');
      return `<article class="clip"><strong>${escapeHtml(clip.title)}</strong><span class="clip-meta">${clip.duration}s · nota ${clip.score} · ${qualityLabel(clip.quality)}</span>${warnings ? `<div class="quality-flags">${warnings}</div>` : ''}${alternatives ? `<details><summary>Títulos alternativos</summary><ul>${alternatives}</ul></details>` : ''}<div class="clip-actions"><a href="/jobs/${job.id}/files/${clip.file}">Baixar MP4</a>${clip.subtitle ? `<a href="/jobs/${job.id}/files/${clip.subtitle}">Legenda</a>` : ''}${youtube}</div></article>`;
    }).join('');
    const retry = job.status === 'failed' ? `<button class="retry" data-retry="${job.id}">Tentar novamente</button>` : '';
    const longform = job.outputs?.length ? `<a class="longform-link" href="/api/jobs/${job.id}/longform-plan" target="_blank" rel="noopener">Plano de vídeo longo</a>` : '';
    return `<article class="job"><div class="job-index">${String(index + 1).padStart(2, '0')}</div><div class="job-main"><h3 class="job-title">${escapeHtml(job.title || 'Vídeo aguardando leitura')}</h3><div class="job-meta"><span>${escapeHtml(job.stage)}</span><span>${job.clip_count} cortes</span>${longform}</div><div class="progress-track"><span style="width:${job.progress}%"></span></div></div><div class="job-state"><div class="status ${job.status}">${statusLabel(job)}</div>${retry}</div>${job.error ? `<div class="error">${escapeHtml(job.error)}</div>` : ''}${clips ? `<div class="clips">${clips}</div>` : ''}</article>`;
  }).join('');
}

function renderStrategy(report) {
  if (report.status !== 'ok') {
    strategyEl.innerHTML = `<p>${escapeHtml(report.message || 'Ainda não há métricas registradas.')}</p>`;
    return;
  }
  const warnings = (report.warnings || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  strategyEl.innerHTML = `<div class="strategy-metrics"><div><b>${Number(report.views).toLocaleString('pt-BR')}</b><span>visualizações</span></div><div><b>${report.watch_hours} h</b><span>exibição</span></div><div><b>${report.subscriber_conversion_per_1000}</b><span>inscritos / mil views</span></div><div><b>${report.computed_avg_view_duration_seconds}s</b><span>média calculada</span></div></div>${warnings ? `<ul class="strategy-warnings">${warnings}</ul>` : ''}`;
}

async function refresh() {
  try {
    const response = await fetch('/api/jobs');
    if (response.ok) renderJobs(await response.json());
  } catch (_) {}
}

async function refreshStrategy() {
  try {
    const response = await fetch('/api/strategy/report');
    if (response.ok) renderStrategy(await response.json());
  } catch (_) {
    strategyEl.textContent = 'Não foi possível carregar as métricas.';
  }
}

async function refreshWorker() {
  try {
    const response = await fetch('/api/worker/status');
    if (!response.ok) return;
    const state = await response.json();
    workerEl.classList.toggle('offline', !state.online);
    workerEl.lastChild.textContent = state.online ? ' PC local online' : ' PC local offline';
  } catch (_) {
    workerEl.classList.add('offline');
    workerEl.lastChild.textContent = ' PC local offline';
  }
}

function discoveryTime(value) {
  if (!value) return '';
  return new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
}

function renderDiscoveryStatus(status) {
  const running = status.state === 'running';
  discoveryButton.disabled = running;
  discoveryButton.setAttribute('aria-busy', String(running));
  discoveryButton.textContent = running ? 'Varredura em andamento…' : 'Rodar varredura agora';
  if (running) {
    discoveryStatusEl.textContent = `Iniciada às ${discoveryTime(status.started_at)}. A pesquisa continua em segundo plano.`;
  } else if (status.state === 'completed') {
    const candidates = Number(status.candidate_count || 0);
    const queued = Number(status.queued_count || 0);
    discoveryStatusEl.textContent = `Última varredura: ${candidates} candidatos · ${queued} enviados para a esteira · ${discoveryTime(status.finished_at)}`;
  } else if (status.state === 'failed') {
    discoveryStatusEl.textContent = 'A última varredura falhou. Tente novamente; os jobs existentes não foram afetados.';
  } else {
    discoveryStatusEl.textContent = 'Pronto para pesquisar. Apenas fontes com licença adequada entram automaticamente na fila.';
  }
}

async function refreshDiscovery() {
  try {
    const response = await fetch('/api/discovery/status');
    if (response.ok) renderDiscoveryStatus(await response.json());
  } catch (_) {
    discoveryStatusEl.textContent = 'Não foi possível consultar o radar agora.';
  }
}

discoveryButton.addEventListener('click', async () => {
  discoveryButton.disabled = true;
  discoveryButton.setAttribute('aria-busy', 'true');
  discoveryButton.textContent = 'Iniciando…';
  discoveryStatusEl.textContent = 'Conectando à pesquisa oficial do YouTube…';
  try {
    const response = await fetch('/api/discovery/run', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Não foi possível iniciar a varredura');
    renderDiscoveryStatus(data);
  } catch (error) {
    discoveryButton.disabled = false;
    discoveryButton.setAttribute('aria-busy', 'false');
    discoveryButton.textContent = 'Rodar varredura agora';
    discoveryStatusEl.textContent = error.message;
  }
});

document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => {
  mode = button.dataset.mode;
  document.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('active', item === button));
  const link = form.querySelector('.url-field');
  const upload = form.querySelector('.upload-field');
  link.hidden = mode !== 'link';
  upload.hidden = mode !== 'upload';
  link.querySelector('input').required = mode === 'link';
  upload.querySelector('input').required = mode === 'upload';
}));

form.addEventListener('submit', async event => {
  event.preventDefault();
  errorEl.textContent = '';
  const button = event.submitter || form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = 'Enfileirando…';
  try {
    const endpoint = mode === 'upload' ? '/api/uploads' : '/api/jobs';
    const response = await fetch(endpoint, { method: 'POST', body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Não foi possível criar os cortes');
    form.reset();
    await refresh();
  } catch (error) {
    errorEl.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = 'Criar cortes';
  }
});

jobsEl.addEventListener('click', async event => {
  const button = event.target.closest('[data-retry]');
  if (!button) return;
  button.disabled = true;
  await fetch(`/api/jobs/${button.dataset.retry}/retry`, { method: 'POST' });
  await refresh();
});

refresh();
refreshStrategy();
refreshWorker();
refreshDiscovery();
setInterval(refresh, 3000);
setInterval(refreshStrategy, 60000);
setInterval(refreshWorker, 10000);
setInterval(refreshDiscovery, 10000);
