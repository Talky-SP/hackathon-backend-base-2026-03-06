/**
 * Talky Mini Spreadsheet — lightweight editable table for the chat UI.
 *
 * Usage:
 *   const sheet = TalkySheet.create(containerEl, data, { title: 'P&L Report' });
 *   sheet.getData();     // get current data as array of row objects
 *   sheet.exportCSV();   // trigger CSV download
 */
const TalkySheet = (() => {

  function create(container, data, opts = {}) {
    if (!data || !data.length) {
      container.innerHTML = '<p style="color:#999;font-size:13px">No data to display</p>';
      return null;
    }

    const title = opts.title || '';
    const columns = Object.keys(data[0]);
    const state = { data: JSON.parse(JSON.stringify(data)), columns };

    // Build wrapper
    const wrapper = document.createElement('div');
    wrapper.className = 'talky-sheet';

    // Toolbar
    const toolbar = document.createElement('div');
    toolbar.className = 'ts-toolbar';
    toolbar.innerHTML = `
      ${title ? `<span class="ts-title">${esc(title)}</span>` : ''}
      <div class="ts-actions">
        <button class="ts-btn ts-btn-add" title="Add row">+ Row</button>
        <button class="ts-btn ts-btn-export" title="Export CSV">⬇ CSV</button>
      </div>
    `;
    wrapper.appendChild(toolbar);

    // Table
    const tableWrap = document.createElement('div');
    tableWrap.className = 'ts-table-wrap';
    const table = document.createElement('table');
    table.className = 'ts-table';

    // Header
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    // Row number column
    headerRow.innerHTML = '<th class="ts-row-num">#</th>';
    columns.forEach(col => {
      const th = document.createElement('th');
      th.textContent = col;
      headerRow.appendChild(th);
    });
    // Delete column
    headerRow.innerHTML += '<th class="ts-row-del"></th>';
    thead.appendChild(headerRow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    wrapper.appendChild(tableWrap);

    // Row count
    const statusBar = document.createElement('div');
    statusBar.className = 'ts-status';
    wrapper.appendChild(statusBar);

    container.appendChild(wrapper);

    // Render rows
    function renderBody() {
      tbody.innerHTML = '';
      state.data.forEach((row, idx) => {
        const tr = document.createElement('tr');
        // Row number
        const numTd = document.createElement('td');
        numTd.className = 'ts-row-num';
        numTd.textContent = idx + 1;
        tr.appendChild(numTd);

        columns.forEach(col => {
          const td = document.createElement('td');
          td.contentEditable = true;
          td.textContent = row[col] != null ? row[col] : '';
          td.addEventListener('blur', () => {
            const val = td.textContent.trim();
            // Try to parse as number
            const num = Number(val);
            state.data[idx][col] = val === '' ? '' : (!isNaN(num) && val !== '' ? num : val);
          });
          td.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') {
              e.preventDefault();
              const next = e.shiftKey ? td.previousElementSibling : td.nextElementSibling;
              if (next && next.contentEditable === 'true') next.focus();
            }
            if (e.key === 'Enter') {
              e.preventDefault();
              td.blur();
            }
          });
          // Right-align numbers
          if (typeof row[col] === 'number') td.classList.add('ts-num');
          tr.appendChild(td);
        });

        // Delete button
        const delTd = document.createElement('td');
        delTd.className = 'ts-row-del';
        delTd.innerHTML = '<span class="ts-del-btn" title="Delete row">&times;</span>';
        delTd.querySelector('.ts-del-btn').addEventListener('click', () => {
          state.data.splice(idx, 1);
          renderBody();
        });
        tr.appendChild(delTd);
        tbody.appendChild(tr);
      });
      statusBar.textContent = `${state.data.length} rows · ${columns.length} columns`;
    }

    renderBody();

    // Add row
    toolbar.querySelector('.ts-btn-add').addEventListener('click', () => {
      const newRow = {};
      columns.forEach(c => newRow[c] = '');
      state.data.push(newRow);
      renderBody();
      // Focus first cell of new row
      const lastRow = tbody.lastElementChild;
      if (lastRow) {
        const firstCell = lastRow.querySelector('td[contenteditable]');
        if (firstCell) firstCell.focus();
      }
    });

    // Export CSV
    toolbar.querySelector('.ts-btn-export').addEventListener('click', () => {
      exportCSV(state.data, columns, title || 'export');
    });

    return {
      getData: () => JSON.parse(JSON.stringify(state.data)),
      exportCSV: () => exportCSV(state.data, columns, title || 'export'),
      destroy: () => wrapper.remove(),
    };
  }

  function exportCSV(data, columns, filename) {
    const BOM = '\uFEFF';
    let csv = BOM;
    csv += columns.map(c => csvEscape(c)).join(',') + '\n';
    data.forEach(row => {
      csv += columns.map(c => csvEscape(row[c] != null ? String(row[c]) : '')).join(',') + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename.replace(/[^a-zA-Z0-9_-]/g, '_') + '.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  function csvEscape(val) {
    if (val.includes(',') || val.includes('"') || val.includes('\n')) {
      return '"' + val.replace(/"/g, '""') + '"';
    }
    return val;
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  return { create };
})();
