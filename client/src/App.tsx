import { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import socket, { connectAuthenticatedSocket } from './socket';
import AdminView from './AdminView';
import AuthGate from './AuthGate';

interface Answer { answer: string; count: number; }
interface QuestionData { question: string; answers: Answer[]; }
interface FullQuestion {
  question: string;
  top_answers: Answer[];
  raw_answers?: string[];
  raw_answers_count?: number;
}
interface GameState {
  current_question_data?: QuestionData | null;
  current_question_idx: number;
  revealed_answers: number[];
  strikes: number;
  team_a_score: number;
  team_b_score: number;
  current_pool: number;
  questions?: FullQuestion[];
}

const AudienceView = () => {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [playStrikeAnim, setPlayStrikeAnim] = useState(false);

  useEffect(() => {
    connectAuthenticatedSocket();
    socket.on('state_update', (state: GameState) => {
       console.log("Got audience update:", state);
       setGameState(state);
    });
    
    socket.on('play_sound', (type: string) => {
       if (type === 'strike') {
          setPlayStrikeAnim(true);
          setTimeout(() => setPlayStrikeAnim(false), 2000);
       }
    });

    return () => { 
       socket.off('state_update'); 
       socket.off('play_sound');
       socket.disconnect();
    };
  }, []);

  if (!gameState || !gameState.current_question_data) 
    return <div className="h-screen w-screen bg-brand-dark text-white flex items-center justify-center text-4xl glow-text animate-pulse">Waiting for Host to Start...</div>;

  const { question, answers } = gameState.current_question_data;
  const maxAnswers = 8; // Family Feud usually has up to 8 answers on the board

  return (
    <div className="h-screen w-screen overflow-hidden bg-brand-dark text-white flex flex-col p-6">
      {/* Title */}
      <h1 className="text-5xl font-display font-black text-center mb-2 tracking-widest text-[#ffcc00] drop-shadow-[0_0_20px_rgba(255,204,0,0.8)]">
        AI FAMILY FEUD
      </h1>
      
      {/* Score Header */}
      <div className="flex justify-between items-center w-full max-w-6xl mx-auto px-12 py-4 mb-6 border-b-4 border-brand-glow">
         <div className="text-5xl bg-brand-blue border-4 border-white px-8 py-3 rounded font-black shadow-[0_0_15px_#fff]">
           TEAM A: {gameState.team_a_score}
         </div>
         <div className="text-6xl bg-black border-4 border-[#00d2ff] text-[#00d2ff] px-12 py-4 rounded-full font-black animate-pulse shadow-[0_0_30px_#00d2ff]">
           POOL: {gameState.current_pool}
         </div>
         <div className="text-5xl bg-brand-blue border-4 border-white px-8 py-3 rounded font-black shadow-[0_0_15px_#fff]">
           TEAM B: {gameState.team_b_score}
         </div>
      </div>

      {/* Main Board Container */}
      <div className="flex-grow flex flex-col items-center justify-start w-full relative z-10 max-w-7xl mx-auto">
        
        {/* Question Panel */}
        <div className="w-full bg-[#001740] px-10 py-6 rounded-2xl border-4 border-[#00d2ff] shadow-[0_0_50px_rgba(0,210,255,0.6)] mb-8 text-center flex items-center justify-center min-h-[120px]">
           <h2 className="text-4xl lg:text-5xl font-bold tracking-wide leading-normal">{question}</h2>
        </div>
        
        {/* Answers Grid */}
        <div className="w-full grid grid-cols-2 gap-x-8 gap-y-6 flex-grow">
          {answers.slice(0, maxAnswers).map((ans, idx) => (
             <div key={idx} className="relative bg-gradient-to-b from-[#1a3b8c] to-[#041a54] rounded-lg border-2 border-brand-glow shadow-[0_0_20px_inset_#000] p-[4px] h-[90px] flex items-center">
               {ans.answer !== '???' ? (
                 <div className="w-full h-full bg-[#0044cc] border-2 border-[#fff] rounded flex items-center justify-between px-6 animate-fade-in-up">
                    <span className="text-3xl font-extrabold uppercase truncate mr-4" style={{textShadow: "2px 2px 0px #000"}}>{idx + 1}. {ans.answer}</span>
                    <span className="text-4xl font-black bg-[#001b54] px-6 py-2 border-l-2 border-r-2 border-[#00d2ff] text-brand-yellow" style={{textShadow: "1px 1px 0px #000"}}>
                      {ans.count}
                    </span>
                 </div>
               ) : (
                 <div className="w-full h-full flex items-center justify-center">
                    <div className="w-16 h-16 rounded-full bg-[#0d2a6b] border-4 border-[#00d2ff] flex items-center justify-center text-4xl font-black opacity-90 shadow-[inset_0_0_15px_#000]">
                       {idx + 1}
                    </div>
                 </div>
               )}
             </div>
          ))}
        </div>

        {/* Big Strike Overlay / Animation */}
        {playStrikeAnim && gameState.strikes > 0 && (
            <div className="absolute inset-0 z-50 flex items-center justify-center pointer-events-none mt-20">
              <div className="bg-black/85 px-20 py-12 rounded-[5rem] border-8 border-red-600 shadow-[0_0_100px_#ff0000] flex gap-12 animate-pulse scale-110">
                  {Array(gameState.strikes).fill('X').map((x, i) => (
                    <span key={i} className="text-[18rem] font-bold text-red-600 leading-none" style={{textShadow: "0 0 40px #ff0000"}}>{x}</span>
                  ))}
              </div>
            </div>
        )}
      </div>
    </div>
  );
};

const HostView = () => {
  const [hostState, setHostState] = useState<GameState | null>(null);
  
  useEffect(() => {
    connectAuthenticatedSocket();
    socket.on('host_state_update', (data: GameState) => {
        console.log("Got host update:", data);
        setHostState(data);
    });
    // Request state manually too, in case socket was already connected!
    socket.emit('reload_database'); // Force an update just in case

    return () => {
      socket.off('host_state_update');
      socket.disconnect();
    };
  }, []);

  if (!hostState || !hostState.questions || hostState.questions.length === 0) {
     return <div className="p-8 text-xl bg-zinc-950 text-white h-screen">Loading Game Database... <br/><br/><button onClick={() => socket.emit('reload_database')} className="bg-blue-600 px-6 py-3 rounded text-xl shadow-lg border border-blue-400">Click to Reload DB</button></div>;
  }
  const currentQ = hostState.questions[hostState.current_question_idx];

  return (
    <div className="min-h-screen bg-zinc-950 p-8 text-white font-sans">
      
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-[#00d2ff]">
           Host Controller <span className="text-zinc-500 text-lg">Question {hostState.current_question_idx + 1} of {hostState.questions.length}</span>
        </h1>
        <button onClick={() => socket.emit('reload_database')} className="border border-zinc-700 bg-zinc-800 text-zinc-400 hover:text-white px-4 py-2 rounded text-sm">Reload Questions DB</button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        
        {/* Left Side: Controller Buttons */}
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-6 flex flex-col gap-6 shadow-2xl h-fit">
           
           <div className="bg-zinc-950 p-4 border border-zinc-800 rounded">
             <h3 className="font-bold text-zinc-400 text-sm tracking-wider mb-3">NAVIGATION</h3>
             <div className="flex gap-3 mb-3">
                <button onClick={() => socket.emit('prev_question')} className="flex-1 bg-zinc-800 hover:bg-zinc-700 py-3 rounded font-bold border border-zinc-700 text-sm">PREV Q</button>
                <button onClick={() => socket.emit('next_question')} className="flex-1 bg-blue-700 hover:bg-blue-600 py-3 rounded font-bold border border-blue-500 shadow-[0_0_15px_rgba(0,100,255,0.4)] text-sm">NEXT Q</button>
             </div>
             <select 
               className="w-full bg-zinc-800 text-zinc-200 border border-zinc-700 rounded p-2 text-sm"
               value={hostState.current_question_idx}
               onChange={(e) => socket.emit('goto_question', { idx: parseInt(e.target.value) })}
             >
               {hostState.questions.map((q: FullQuestion, idx: number) => (
                 <option key={idx} value={idx}>Q{idx + 1}: {q.question}</option>
               ))}
             </select>
           </div>

           <div className="bg-zinc-950 p-4 border border-zinc-800 rounded relative overflow-hidden">
             {/* Red Glow logic */}
             {hostState.strikes > 0 && <div className="absolute inset-0 bg-red-900/10 pointer-events-none"></div>}
             <h3 className="font-bold text-red-500 text-sm tracking-wider mb-3">STRIKES (Current: {hostState.strikes}/3)</h3>
             <div className="flex flex-col gap-3">
                <button onClick={() => socket.emit('show_strike')} className="w-full bg-red-700 hover:bg-red-600 py-4 rounded font-bold shadow-[0_0_15px_rgba(200,0,0,0.5)] border border-red-500 text-lg">GIVE STRIKE (X)</button>
                <button onClick={() => socket.emit('clear_strikes')} className="w-full bg-zinc-800 hover:bg-zinc-700 py-2 rounded font-bold border border-zinc-700">Clear Strikes</button>
             </div>
           </div>

           <div className="bg-zinc-950 p-4 border border-zinc-800 rounded">
             <h3 className="font-bold text-yellow-500 text-sm tracking-wider mb-3">CURRENT POOL: {hostState.current_pool} pts</h3>
             <div className="flex gap-3">
                <button onClick={() => socket.emit('award_points', { team: 'team_a' })} className="flex-1 bg-green-700 hover:bg-green-600 py-3 rounded font-bold border border-green-500 shadow-[0_0_15px_rgba(0,250,50,0.3)]">Win Team A</button>
                <button onClick={() => socket.emit('award_points', { team: 'team_b' })} className="flex-1 bg-green-700 hover:bg-green-600 py-3 rounded font-bold border border-green-500 shadow-[0_0_15px_rgba(0,250,50,0.3)]">Win Team B</button>
             </div>
             
             {/* Score modifier controls purely for manual adjustment */}
              <div className="mt-4 border-t border-zinc-800 pt-4 flex gap-4 text-center">
                 <div className="w-1/2">
                   <div className="text-xs text-zinc-500 mb-1">Set Team A Score</div>
                   <input 
                     type="number" 
                     className="w-full bg-zinc-800 text-center text-white py-1 rounded border border-zinc-700 text-sm" 
                     value={hostState.team_a_score || 0} 
                     onChange={(e) => socket.emit('set_score', {team: 'team_a', score: parseInt(e.target.value) || 0})}
                   />
                 </div>
                 <div className="w-1/2">
                   <div className="text-xs text-zinc-500 mb-1">Set Team B Score</div>
                   <input 
                     type="number" 
                     className="w-full bg-zinc-800 text-center text-white py-1 rounded border border-zinc-700 text-sm" 
                     value={hostState.team_b_score || 0} 
                     onChange={(e) => socket.emit('set_score', {team: 'team_b', score: parseInt(e.target.value) || 0})}
                   />
                 </div>
              </div>
           </div>

        </div>

        {/* Right Side: Questions DB */}
        <div className="lg:col-span-3 bg-zinc-900 rounded-xl border border-zinc-800 p-8 shadow-2xl flex flex-col gap-6">
           <div className="bg-[#001740] p-6 border-l-4 border-blue-500 rounded flex items-center">
              <h2 className="text-2xl font-bold leading-relaxed">{currentQ.question}</h2>
           </div>
           
           <div className="flex justify-between items-center text-sm font-bold text-zinc-400 -mb-2 px-2">
              <span>Answers (Click to Reveal on TV)</span>
              <span>Points</span>
           </div>

           <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {currentQ.top_answers.map((ans: Answer, idx: number) => {
                 const revealed = hostState.revealed_answers.includes(idx);
                 return (
                   <button 
                     key={idx}
                     onClick={() => !revealed && socket.emit('reveal_answer', idx)}
                     className={`w-full p-4 rounded text-left flex justify-between items-center text-xl font-bold transition-all border-l-4 ${
                       revealed 
                         ? 'bg-zinc-800 border-green-500 text-green-500 opacity-60 cursor-default shadow-inner' 
                         : 'bg-zinc-800 border-[#00d2ff] hover:bg-zinc-700 hover:-translate-y-[2px] cursor-pointer text-white shadow-lg'
                     }`}
                   >
                      <div className="flex items-center gap-3">
                        <span className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${revealed ? 'bg-green-900 text-green-400' : 'bg-blue-900'}`}>{idx + 1}</span>
                        <span>{ans.answer}</span>
                      </div>
                      <span className="bg-black px-4 py-1 rounded border border-zinc-800 text-[#ffcc00] font-black">{ans.count}</span>
                   </button>
                 )
              })}
           </div>
        </div>

      </div>
    </div>
  )
}

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={
           <div className="h-screen w-screen bg-zinc-950 flex flex-col items-center justify-center gap-12 text-white overflow-hidden relative">
              <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-blue-900/20 via-zinc-950 to-zinc-950"></div>
              
              <div className="relative z-10 text-center">
                 <h1 className="text-7xl font-black mb-4 bg-clip-text text-transparent bg-gradient-to-br from-[#00d2ff] to-[#0055ff] filter drop-shadow-[0_0_20px_rgba(0,210,255,0.4)]">
                    AI Family Feud
                 </h1>
                 <p className="text-xl text-zinc-400 tracking-[0.2em] font-bold">GAME SHOW DASHBOARD</p>
              </div>

              <div className="relative z-10 flex gap-8 mt-4">
                 <Link to="/audience" target="_blank" className="group px-12 py-6 bg-gradient-to-b from-[#1a4bba] to-[#0d2a75] rounded-2xl font-bold shadow-[0_0_30px_rgba(0,100,255,0.3)] hover:shadow-[0_0_50px_rgba(0,100,255,0.6)] border-2 border-[#00d2ff] transition-all hover:-translate-y-2 flex flex-col items-center gap-2">
                    <span className="text-3xl text-white group-hover:text-[#00d2ff]">📺 Audience Screen</span>
                    <span className="text-sm text-blue-300">Open Board Viewer</span>
                 </Link>
                 <Link to="/host" target="_blank" className="group px-12 py-6 bg-zinc-800 rounded-2xl font-bold border-2 border-zinc-600 hover:border-zinc-400 hover:bg-zinc-700 shadow-xl transition-all hover:-translate-y-2 flex flex-col items-center gap-2">
                    <span className="text-3xl text-white group-hover:text-zinc-300">🎤 Host Controller</span>
                    <span className="text-sm text-zinc-400">Open Game Panel</span>
                 </Link>
                 <Link to="/admin" target="_blank" className="group px-12 py-6 bg-emerald-900 rounded-2xl font-bold border-2 border-emerald-600 hover:border-emerald-400 hover:bg-emerald-800 shadow-xl transition-all hover:-translate-y-2 flex flex-col items-center gap-2">
                    <span className="text-3xl text-white group-hover:text-emerald-200">Admin Panel</span>
                    <span className="text-sm text-emerald-300">Edit Questions DB</span>
                 </Link>
              </div>
           </div>
        } />
        <Route path="/audience" element={<AuthGate title="Audience Screen"><AudienceView /></AuthGate>} />
        <Route path="/host" element={<AuthGate title="Host Controller"><HostView /></AuthGate>} />
        <Route path="/admin" element={<AuthGate title="Admin Panel"><AdminView /></AuthGate>} />
      </Routes>
    </Router>
  );
}

export default App;
