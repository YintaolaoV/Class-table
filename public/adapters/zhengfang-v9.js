/*
 * V课表公测版：正方教务 V9 Safari 快捷指令适配器
 * 用法：在 Safari 的“在网页上运行 JavaScript”动作中粘贴本文件内容。
 * 适配器只读取当前页面 DOM，结果通过 completion(JSON.stringify(...)) 返回。
 */
(function () {
  'use strict';
  const text = value => String(value || '').replace(/\s+/g, ' ').trim();
  const range = value => {
    const raw = text(value).replace(/第|周/g, '');
    const values = [];
    raw.split(/[,，、;；\s]+/).forEach(part => {
      const match = part.match(/(\d+)\s*[-—~至]\s*(\d+)/);
      if (match) { for (let n = +match[1]; n <= +match[2] && values.length < 40; n += 1) values.push(n); }
      else if (/^\d+$/.test(part)) values.push(+part);
    });
    return [...new Set(values)].filter(n => n >= 1 && n <= 40).sort((a, b) => a - b);
  };
  const output = { schemaVersion: 1, adapterId: 'zhengfang-v9-html', courses: [] };
  const cells = [...document.querySelectorAll('#kbgrid_table_0 td.td_wrap, #kblist_table td')];
  cells.forEach(cell => {
    const id = cell.id || '';
    const idMatch = id.match(/(\d+)[-_](\d+)/);
    const day = idMatch ? +idMatch[1] : +(cell.dataset.day || 0);
    const start = idMatch ? +idMatch[2] : +(cell.dataset.section || 0);
    if (!(day >= 1 && day <= 7 && start >= 1)) return;
    const title = text(cell.querySelector('.title, .course-name, a')?.textContent || cell.textContent).split(/\(|（/)[0];
    if (!title) return;
    const raw = text(cell.textContent);
    const sectionMatch = raw.match(/(\d+)\s*[-—~至]\s*(\d+)\s*节/);
    const weeks = range(raw.match(/([0-9、，,\-—~至\s]+)周/)?.[1] || '');
    const location = text(cell.querySelector('.td_wrap, .address, .place')?.textContent || '').replace(title, '');
    output.courses.push({ name: title.slice(0, 80), location: location.slice(0, 100), day, start, duration: sectionMatch ? Math.max(1, +sectionMatch[2] - +sectionMatch[1] + 1) : 1, weeks });
  });
  output.courses = output.courses.filter((course, index, list) => list.findIndex(item => JSON.stringify(item) === JSON.stringify(course)) === index);
  completion(JSON.stringify(output));
})();
