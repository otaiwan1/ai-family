import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Database,
  Download,
  Eye,
  EyeOff,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { authenticatedFetch } from './auth';

interface Answer {
  answer: string;
  count: number;
}

interface QuestionSummary {
  index: number;
  question: string;
  top_answers: Answer[];
  raw_answers_count: number;
  model_counts: Record<string, number>;
  updated_at?: string;
}

interface QuestionDetail extends QuestionSummary {
  raw_answers: string[];
}

interface GenerationJob {
  status: 'idle' | 'running' | 'completed' | 'failed';
  question: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  raw_answers_count: number | null;
  target_count: number | null;
}

interface Notice {
  type: 'success' | 'error';
  text: string;
}

const emptyAnswer = (): Answer => ({ answer: '', count: 0 });

const AdminView = () => {
  const [questions, setQuestions] = useState<QuestionSummary[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [expectedQuestion, setExpectedQuestion] = useState('');
  const [questionText, setQuestionText] = useState('');
  const [answers, setAnswers] = useState<Answer[]>([emptyAnswer()]);
  const [rawAnswers, setRawAnswers] = useState<string[]>([]);
  const [rawAnswersCount, setRawAnswersCount] = useState(0);
  const [modelCounts, setModelCounts] = useState<Record<string, number>>({});
  const [search, setSearch] = useState('');
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [showRawAnswers, setShowRawAnswers] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [targetCount, setTargetCount] = useState(100);
  const [renormalize, setRenormalize] = useState(true);
  const [resetExisting, setResetExisting] = useState(false);
  const adminFetch = useCallback(async (path: string, init: RequestInit = {}) => {
    const response = await authenticatedFetch(path, init);
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch {
        // Keep the HTTP status when the server did not return JSON.
      }
      throw new Error(detail);
    }
    return response;
  }, []);

  const applyDetail = useCallback((detail: QuestionDetail) => {
    setSelectedIndex(detail.index);
    setExpectedQuestion(detail.question);
    setQuestionText(detail.question);
    setAnswers(detail.top_answers.length ? detail.top_answers : [emptyAnswer()]);
    setRawAnswers(detail.raw_answers || []);
    setRawAnswersCount(detail.raw_answers_count || 0);
    setModelCounts(detail.model_counts || {});
    setDirty(false);
  }, []);

  const loadDetail = useCallback(async (index: number) => {
    setLoading(true);
    try {
      const response = await adminFetch(`/api/admin/questions/${index}`);
      applyDetail(await response.json());
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '無法載入題目' });
    } finally {
      setLoading(false);
    }
  }, [adminFetch, applyDetail]);

  const loadQuestions = useCallback(async (preferredIndex?: number | null) => {
    setLoading(true);
    try {
      const response = await adminFetch('/api/admin/questions');
      const data: { questions: QuestionSummary[] } = await response.json();
      setQuestions(data.questions);
      const nextIndex = preferredIndex ?? data.questions[0]?.index ?? null;
      if (nextIndex !== null && data.questions.some((item) => item.index === nextIndex)) {
        await loadDetail(nextIndex);
      } else if (!data.questions.length) {
        setSelectedIndex(null);
        setExpectedQuestion('');
        setQuestionText('');
        setAnswers([emptyAnswer()]);
        setRawAnswers([]);
        setRawAnswersCount(0);
        setModelCounts({});
        setDirty(false);
      }
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '無法載入題庫' });
    } finally {
      setLoading(false);
    }
  }, [adminFetch, loadDetail]);

  const loadJob = useCallback(async () => {
    try {
      const response = await adminFetch('/api/admin/generation-job');
      setJob(await response.json());
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '無法讀取 AI 工作狀態' });
    }
  }, [adminFetch]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadQuestions();
      void loadJob();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadJob, loadQuestions]);

  useEffect(() => {
    if (job?.status !== 'running') return;
    const timer = window.setInterval(async () => {
      try {
        const response = await adminFetch('/api/admin/generation-job');
        const nextJob: GenerationJob = await response.json();
        setJob(nextJob);
        if (nextJob.status === 'completed') {
          setNotice({ type: 'success', text: `AI 已補齊 ${nextJob.raw_answers_count} 筆回答並更新分布` });
          await loadQuestions(selectedIndex);
        } else if (nextJob.status === 'failed') {
          setNotice({ type: 'error', text: nextJob.error || 'AI 補齊失敗' });
        }
      } catch (error) {
        setNotice({ type: 'error', text: error instanceof Error ? error.message : '無法更新 AI 工作狀態' });
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [adminFetch, job?.status, loadQuestions, selectedIndex]);

  const filteredQuestions = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return questions;
    return questions.filter((item) =>
      item.question.toLowerCase().includes(keyword)
      || item.top_answers.some((answer) => answer.answer.toLowerCase().includes(keyword)),
    );
  }, [questions, search]);

  const pointsTotal = answers.reduce((sum, answer) => sum + (Number(answer.count) || 0), 0);

  function startNewQuestion(checkDirty = true) {
    if (checkDirty && dirty && !window.confirm('目前修改尚未儲存，確定要建立新題目？')) return;
    setSelectedIndex(null);
    setExpectedQuestion('');
    setQuestionText('');
    setAnswers([emptyAnswer()]);
    setRawAnswers([]);
    setRawAnswersCount(0);
    setModelCounts({});
    setDirty(false);
    setNotice(null);
  }

  async function selectQuestion(index: number) {
    if (index === selectedIndex) return;
    if (dirty && !window.confirm('目前修改尚未儲存，確定切換題目？')) return;
    await loadDetail(index);
    if (window.innerWidth < 1024) {
      document.getElementById('admin-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function updateAnswer(index: number, patch: Partial<Answer>) {
    setAnswers((current) => current.map((answer, answerIndex) => (
      answerIndex === index ? { ...answer, ...patch } : answer
    )));
    setDirty(true);
  }

  function moveAnswer(index: number, offset: number) {
    const target = index + offset;
    if (target < 0 || target >= answers.length) return;
    setAnswers((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setDirty(true);
  }

  async function saveQuestion() {
    const cleanedAnswers = answers
      .map((answer) => ({ answer: answer.answer.trim(), count: Number(answer.count) || 0 }))
      .filter((answer) => answer.answer);
    if (!questionText.trim()) {
      setNotice({ type: 'error', text: '題目文字不能空白' });
      return;
    }
    setSaving(true);
    try {
      const isNew = selectedIndex === null;
      const response = await adminFetch(
        isNew ? '/api/admin/questions' : `/api/admin/questions/${selectedIndex}`,
        {
          method: isNew ? 'POST' : 'PUT',
          body: JSON.stringify({
            question: questionText.trim(),
            top_answers: cleanedAnswers,
            expected_question: isNew ? undefined : expectedQuestion,
          }),
        },
      );
      const data = await response.json();
      setNotice({ type: 'success', text: isNew ? '題目已新增' : '題目與答案分布已儲存' });
      await loadQuestions(data.index);
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '儲存失敗' });
    } finally {
      setSaving(false);
    }
  }

  async function deleteQuestion() {
    if (selectedIndex === null) return;
    if (!window.confirm(`確定刪除「${expectedQuestion}」？刪除前會自動備份資料庫。`)) return;
    try {
      await adminFetch(`/api/admin/questions/${selectedIndex}`, { method: 'DELETE' });
      setNotice({ type: 'success', text: '題目已刪除，原始 DB 已備份' });
      setSelectedIndex(null);
      await loadQuestions(Math.max(0, selectedIndex - 1));
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '刪除失敗' });
    }
  }

  async function startFill() {
    if (selectedIndex === null) {
      setNotice({ type: 'error', text: '請先儲存新題目，再執行 AI 補齊' });
      return;
    }
    if (dirty) {
      setNotice({ type: 'error', text: '請先儲存目前修改，再執行 AI 補齊' });
      return;
    }
    try {
      const response = await adminFetch(`/api/admin/questions/${selectedIndex}/fill`, {
        method: 'POST',
        body: JSON.stringify({
          target_count: targetCount,
          renormalize,
          reset_existing: resetExisting,
        }),
      });
      setJob(await response.json());
      setNotice({ type: 'success', text: 'AI 補齊工作已開始，可以繼續編輯其他題目' });
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '無法啟動 AI 補齊' });
    }
  }

  async function exportDatabase() {
    try {
      const response = await adminFetch('/api/admin/export');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'questions_db.json';
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice({ type: 'error', text: error instanceof Error ? error.message : '匯出失敗' });
    }
  }

  return (
    <div className="min-h-screen bg-[#f4f6f8] text-[#17202a]">
      <header className="sticky top-0 z-30 flex min-h-16 flex-col items-stretch justify-between gap-3 border-b border-[#d8dde3] bg-white px-4 py-3 sm:flex-row sm:items-center sm:px-5">
        <div className="flex items-center gap-3">
          <Database className="h-6 w-6 text-[#136f63]" />
          <div>
            <h1 className="text-lg font-bold">AI Family Feud Admin</h1>
            <p className="text-xs text-[#65717f]">題庫與答案分布管理</p>
          </div>
        </div>
        <div className="grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap sm:justify-end">
          <button type="button" onClick={() => void exportDatabase()} className="flex items-center gap-2 border border-[#cbd2d9] bg-white px-3 py-2 text-sm font-semibold hover:bg-[#eef1f4]">
            <Download className="h-4 w-4" /> 匯出 DB
          </button>
          <button type="button" onClick={() => void loadQuestions(selectedIndex)} className="flex items-center gap-2 border border-[#cbd2d9] bg-white px-3 py-2 text-sm font-semibold hover:bg-[#eef1f4]">
            <RefreshCw className="h-4 w-4" /> 重新載入
          </button>
        </div>
      </header>

      {notice && (
        <div className={`flex items-center justify-between border-b px-5 py-3 text-sm ${notice.type === 'success' ? 'border-[#9ac7bd] bg-[#e6f4f1] text-[#145c52]' : 'border-[#e1a8a8] bg-[#fff0f0] text-[#9b2525]'}`}>
          <span className="flex items-center gap-2">
            {notice.type === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
            {notice.text}
          </span>
          <button type="button" onClick={() => setNotice(null)} title="關閉訊息" className="p-1"><X className="h-4 w-4" /></button>
        </div>
      )}

      <main className="grid min-h-[calc(100vh-64px)] grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="border-r border-[#d8dde3] bg-white">
          <div className="border-b border-[#d8dde3] p-4">
            <button type="button" onClick={() => startNewQuestion()} className="flex w-full items-center justify-center gap-2 bg-[#136f63] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#0f5d53]">
              <Plus className="h-4 w-4" /> 新增題目
            </button>
            <div className="relative mt-3">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-[#7b8794]" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜尋題目或答案"
                className="w-full border border-[#cbd2d9] py-2 pl-9 pr-3 text-sm outline-none focus:border-[#136f63]"
              />
            </div>
            <p className="mt-2 text-xs text-[#65717f]">{filteredQuestions.length} / {questions.length} 題</p>
          </div>
          <div className="max-h-[32vh] overflow-y-auto lg:max-h-[calc(100vh-190px)]">
            {filteredQuestions.map((item) => (
              <button
                type="button"
                key={`${item.index}-${item.question}`}
                onClick={() => void selectQuestion(item.index)}
                className={`w-full border-b border-[#e4e8ec] px-4 py-3 text-left hover:bg-[#f2f7f6] ${selectedIndex === item.index ? 'border-l-4 border-l-[#136f63] bg-[#eaf4f2]' : 'border-l-4 border-l-transparent'}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="line-clamp-2 text-sm font-semibold leading-5">{item.question}</span>
                  <span className="shrink-0 text-xs tabular-nums text-[#65717f]">#{item.index + 1}</span>
                </div>
                <div className="mt-2 flex gap-3 text-xs text-[#65717f]">
                  <span>{item.top_answers.length} 選項</span>
                  <span>{item.raw_answers_count} raw</span>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section id="admin-editor" className="min-w-0 scroll-mt-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#d8dde3] bg-white px-6 py-4">
            <div>
              <h2 className="text-xl font-bold">{selectedIndex === null ? '新增題目' : `編輯題目 #${selectedIndex + 1}`}</h2>
              <p className="mt-1 text-sm text-[#65717f]">修改題目文字、上榜答案與分數分布</p>
            </div>
            <div className="flex items-center gap-2">
              {dirty && <span className="text-xs font-semibold text-[#a35c00]">尚未儲存</span>}
              {selectedIndex !== null && (
                <button type="button" onClick={() => void deleteQuestion()} className="flex items-center gap-2 border border-[#d49393] bg-white px-3 py-2 text-sm font-semibold text-[#a32d2d] hover:bg-[#fff0f0]">
                  <Trash2 className="h-4 w-4" /> 刪除
                </button>
              )}
              <button type="button" disabled={saving} onClick={() => void saveQuestion()} className="flex items-center gap-2 bg-[#17202a] px-4 py-2 text-sm font-bold text-white hover:bg-black disabled:opacity-50">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} 儲存
              </button>
            </div>
          </div>

          <div className="space-y-6 p-6">
            <div>
              <label htmlFor="admin-question" className="mb-2 block text-xs font-bold uppercase text-[#52606d]">題目</label>
              <textarea
                id="admin-question"
                rows={3}
                value={questionText}
                onChange={(event) => { setQuestionText(event.target.value); setDirty(true); }}
                className="w-full resize-y border border-[#b8c1ca] bg-white px-4 py-3 text-lg font-semibold leading-7 outline-none focus:border-[#136f63] focus:ring-1 focus:ring-[#136f63]"
                placeholder="輸入新的 Family Feud 題目"
              />
            </div>

            <div className="border border-[#d8dde3] bg-white">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#d8dde3] px-4 py-3">
                <div>
                  <h3 className="font-bold">上榜答案與分布</h3>
                  <p className="text-xs text-[#65717f]">目前 {answers.filter((answer) => answer.answer.trim()).length} 個選項，顯示分數合計 {pointsTotal}</p>
                </div>
                <button type="button" onClick={() => { setAnswers((current) => [...current, emptyAnswer()]); setDirty(true); }} className="flex items-center gap-2 border border-[#9fbab5] px-3 py-2 text-sm font-semibold text-[#136f63] hover:bg-[#eaf4f2]">
                  <Plus className="h-4 w-4" /> 新增選項
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[620px] border-collapse text-sm">
                  <thead className="bg-[#f1f3f5] text-left text-xs uppercase text-[#52606d]">
                    <tr><th className="w-16 px-4 py-2">排名</th><th className="px-3 py-2">回答文字</th><th className="w-36 px-3 py-2">分布 / 分數</th><th className="w-36 px-3 py-2 text-right">操作</th></tr>
                  </thead>
                  <tbody>
                    {answers.map((answer, index) => (
                      <tr key={index} className="border-t border-[#e4e8ec]">
                        <td className="px-4 py-3 font-bold tabular-nums text-[#65717f]">{index + 1}</td>
                        <td className="px-3 py-3">
                          <input value={answer.answer} onChange={(event) => updateAnswer(index, { answer: event.target.value })} className="w-full border border-[#cbd2d9] px-3 py-2 outline-none focus:border-[#136f63]" placeholder="答案文字" />
                        </td>
                        <td className="px-3 py-3">
                          <input type="number" min="0" value={answer.count} onChange={(event) => updateAnswer(index, { count: Number(event.target.value) || 0 })} className="w-full border border-[#cbd2d9] px-3 py-2 tabular-nums outline-none focus:border-[#136f63]" />
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex justify-end gap-1">
                            <button type="button" title="向上移動" disabled={index === 0} onClick={() => moveAnswer(index, -1)} className="p-2 text-[#52606d] hover:bg-[#eef1f4] disabled:opacity-25"><ArrowUp className="h-4 w-4" /></button>
                            <button type="button" title="向下移動" disabled={index === answers.length - 1} onClick={() => moveAnswer(index, 1)} className="p-2 text-[#52606d] hover:bg-[#eef1f4] disabled:opacity-25"><ArrowDown className="h-4 w-4" /></button>
                            <button type="button" title="刪除選項" onClick={() => { setAnswers((current) => current.filter((_, answerIndex) => answerIndex !== index)); setDirty(true); }} className="p-2 text-[#a32d2d] hover:bg-[#fff0f0]"><Trash2 className="h-4 w-4" /></button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="border border-[#b9d7d1] bg-[#eef7f5]">
              <div className="flex flex-wrap items-start justify-between gap-4 px-4 py-4">
                <div>
                  <div className="flex items-center gap-2 font-bold text-[#145c52]"><Sparkles className="h-5 w-5" /> AI 補齊回答</div>
                  <p className="mt-1 text-sm text-[#476b66]">目前 {rawAnswersCount} 筆 raw answers。工作在背景執行，可以繼續編輯其他題目。</p>
                  {job?.status === 'running' && (
                    <div className="mt-3 flex items-center gap-2 text-sm font-semibold text-[#145c52]">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {job.question}: {job.raw_answers_count ?? 0} / {job.target_count}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="text-xs font-bold text-[#476b66]">目標數量
                    <input type="number" min="1" max="1000" value={targetCount} onChange={(event) => setTargetCount(Number(event.target.value) || 100)} className="mt-1 block w-24 border border-[#9fbab5] bg-white px-3 py-2 text-sm text-[#17202a] outline-none" />
                  </label>
                  <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={renormalize} onChange={(event) => setRenormalize(event.target.checked)} /> 重新計算分布</label>
                  <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={resetExisting} onChange={(event) => setResetExisting(event.target.checked)} /> 先清空舊回答</label>
                  <button type="button" disabled={job?.status === 'running'} onClick={() => void startFill()} className="flex items-center gap-2 bg-[#136f63] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#0f5d53] disabled:opacity-50">
                    {job?.status === 'running' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} 開始補齊
                  </button>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <div className="border border-[#d8dde3] bg-white">
                <div className="border-b border-[#d8dde3] px-4 py-3"><h3 className="font-bold">模型回答分布</h3></div>
                <div className="divide-y divide-[#e4e8ec]">
                  {Object.entries(modelCounts).length ? Object.entries(modelCounts).map(([model, count]) => (
                    <div key={model} className="flex items-center justify-between gap-4 px-4 py-3 text-sm"><span className="truncate text-[#52606d]">{model}</span><strong className="tabular-nums">{count}</strong></div>
                  )) : <p className="px-4 py-5 text-sm text-[#7b8794]">尚無模型回答紀錄</p>}
                </div>
              </div>

              <div className="border border-[#d8dde3] bg-white">
                <div className="flex items-center justify-between border-b border-[#d8dde3] px-4 py-3">
                  <h3 className="font-bold">Raw answers ({rawAnswers.length})</h3>
                  <button type="button" onClick={() => setShowRawAnswers((current) => !current)} className="flex items-center gap-2 text-sm font-semibold text-[#136f63]">
                    {showRawAnswers ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />} {showRawAnswers ? '隱藏' : '查看'}
                  </button>
                </div>
                {showRawAnswers ? (
                  <div className="max-h-64 overflow-y-auto p-3">
                    <div className="flex flex-wrap gap-2">
                      {rawAnswers.map((answer, index) => <span key={`${answer}-${index}`} className="border border-[#d8dde3] bg-[#f7f8f9] px-2 py-1 text-xs">{answer}</span>)}
                    </div>
                  </div>
                ) : <p className="px-4 py-5 text-sm text-[#7b8794]">原始回答預設隱藏，避免大量內容干擾編輯。</p>}
              </div>
            </div>
          </div>
        </section>
      </main>

      {loading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-white/60">
          <Loader2 className="h-7 w-7 animate-spin text-[#136f63]" />
        </div>
      )}
    </div>
  );
};

export default AdminView;
