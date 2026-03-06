/* Dashboard: Chart.js bar chart + sortable table + live filter */

// ── Chart ──────────────────────────────────────────────────
const top15 = ITEMS.slice(0, 15);

const ctx = document.getElementById('topItemsChart').getContext('2d');
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: top15.map(i => i.name.length > 24 ? i.name.slice(0, 24) + '…' : i.name),
    datasets: [{
      label: 'Times Ordered',
      data: top15.map(i => i.qty),
      backgroundColor: top15.map((_, idx) =>
        `hsl(${355 - idx * 7}, 70%, ${48 + idx * 1.2}%)`
      ),
      borderRadius: 6,
      borderSkipped: false,
    }],
  },
  options: {
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: ctx => ` ${ctx.raw} times ordered`,
        },
      },
    },
    scales: {
      x: {
        grid: { color: 'rgba(255,255,255,0.05)' },
        ticks: { color: '#8a9ba8', font: { family: 'Space Grotesk' } },
      },
      y: {
        grid: { display: false },
        ticks: { color: '#f1faee', font: { family: 'Space Grotesk', size: 13 } },
      },
    },
  },
});

// ── Table ───────────────────────────────────────────────────
let tableData = [...ITEMS];
let sortState = { col: 'rank', dir: 'asc' };

function buildTable(data) {
  const tbody = document.querySelector('#items-table tbody');
  tbody.innerHTML = '';
  data.forEach(item => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${item.rank}</td><td>${escHtml(item.name)}</td><td>${item.qty}</td>`;
    tbody.appendChild(tr);
  });
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Sort on header click
document.querySelectorAll('#items-table th[data-col]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (sortState.col === col) {
      sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      sortState.col = col;
      sortState.dir = 'asc';
    }
    applySort();
  });
});

function applySort() {
  const query = document.getElementById('table-search').value.toLowerCase();
  let data = ITEMS.filter(i => i.name.toLowerCase().includes(query));
  const col = sortState.col;
  data = [...data].sort((a, b) => {
    const va = a[col], vb = b[col];
    const cmp = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
    return sortState.dir === 'asc' ? cmp : -cmp;
  });
  buildTable(data);
}

// Live search filter
document.getElementById('table-search').addEventListener('input', applySort);

// Initial render (already rendered by Jinja, but set up sort on initial state)
// No rebuild needed on load — Jinja rendered the initial rows
