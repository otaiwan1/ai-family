import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { KeyRound, Loader2, LockKeyhole } from 'lucide-react';
import { authenticatedFetch, clearAccessToken, getAccessToken, setAccessToken } from './auth';

interface AuthGateProps {
  children: ReactNode;
  title: string;
}

const AuthGate = ({ children, title }: AuthGateProps) => {
  const [status, setStatus] = useState<'checking' | 'locked' | 'authenticated'>('checking');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(async () => {
      if (!getAccessToken()) {
        setStatus('locked');
        return;
      }
      try {
        const response = await authenticatedFetch('/api/auth/me');
        if (!response.ok) throw new Error('Session expired');
        setStatus('authenticated');
      } catch {
        clearAccessToken();
        setStatus('locked');
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || '密碼錯誤');
      }
      const data: { token: string } = await response.json();
      setAccessToken(data.token);
      setPassword('');
      setStatus('authenticated');
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : '登入失敗');
    } finally {
      setSubmitting(false);
    }
  }

  if (status === 'authenticated') return children;

  if (status === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#07111f] text-white">
        <Loader2 className="h-7 w-7 animate-spin text-[#00d2ff]" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#07111f] px-5 text-white">
      <form onSubmit={submit} className="w-full max-w-sm border border-[#29405a] bg-[#0d1b2b] p-7 shadow-2xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center bg-[#123b4b] text-[#34d1bf]">
            <LockKeyhole className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-xl font-bold">{title}</h1>
            <p className="mt-1 text-sm text-[#9eb0c3]">AI Family Feud 存取驗證</p>
          </div>
        </div>
        <label htmlFor="access-password" className="mb-2 block text-xs font-bold uppercase text-[#9eb0c3]">密碼</label>
        <div className="flex border border-[#40566e] bg-[#081421] focus-within:border-[#34d1bf]">
          <KeyRound className="ml-3 mt-3 h-5 w-5 shrink-0 text-[#8397aa]" />
          <input
            id="access-password"
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="min-w-0 flex-1 bg-transparent px-3 py-3 outline-none"
            placeholder="輸入存取密碼"
          />
        </div>
        {error && <p className="mt-3 text-sm font-semibold text-[#ff8d8d]">{error}</p>}
        <button type="submit" disabled={submitting || !password} className="mt-5 flex w-full items-center justify-center gap-2 bg-[#137c70] px-4 py-3 font-bold hover:bg-[#189486] disabled:opacity-50">
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          進入 {title}
        </button>
      </form>
    </div>
  );
};

export default AuthGate;
