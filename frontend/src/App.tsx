import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

type FlashCard = {
  id: string;
  index: number;
  label: string;
  image_url: string | null;
  selected: boolean;
};

type SessionResponse = {
  session_id: string;
  status: string;
  child_name: string;
  event_name: string;
  cards: FlashCard[];
  selected_cards: FlashCard[];
  audio_url: string | null;
};

type ParentForm = {
  child_name: string;
  age: number;
  event_name: string;
  caregiver_name: string;
  suggested_steps: string;
  activity_count: number;
};

const INITIAL_FORM: ParentForm = {
  child_name: "",
  age: 7,
  event_name: "",
  caregiver_name: "",
  suggested_steps: "",
  activity_count: 5,
};

const INITIAL_STATUS = "Fill in the parent setup, then create picture cards for the child.";

function audioMimeType(url: string): string | undefined {
  if (url.endsWith(".mp3")) {
    return "audio/mpeg";
  }
  if (url.endsWith(".wav")) {
    return "audio/wav";
  }
  return undefined;
}

async function parseApiError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string };
    return data.detail || "Something went wrong.";
  } catch {
    return "Something went wrong.";
  }
}

async function createSession(payload: ParentForm): Promise<SessionResponse> {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as SessionResponse;
}

async function selectCard(sessionId: string, choiceIndex: number): Promise<SessionResponse> {
  const response = await fetch(`/api/session/${sessionId}/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choice_index: choiceIndex }),
  });
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as SessionResponse;
}

async function playCompletePlan(sessionId: string): Promise<SessionResponse> {
  const response = await fetch(`/api/session/${sessionId}/play-plan`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as SessionResponse;
}

async function prewarmAudio(sessionId: string): Promise<void> {
  await fetch(`/api/session/${sessionId}/prewarm-audio`, {
    method: "POST",
  });
}

function App() {
  const [form, setForm] = useState<ParentForm>(INITIAL_FORM);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [status, setStatus] = useState(INITIAL_STATUS);
  const [loadingCards, setLoadingCards] = useState(false);
  const [loadingChoiceIndex, setLoadingChoiceIndex] = useState<number | null>(null);
  const [playingFullPlan, setPlayingFullPlan] = useState(false);
  const [audioPlaybackToken, setAudioPlaybackToken] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playPlanButtonRef = useRef<HTMLButtonElement | null>(null);

  async function playAudioUrl(audioUrl: string | null): Promise<boolean> {
    if (!audioUrl || !audioRef.current) {
      return false;
    }
    const playableUrl = `${audioUrl}${audioUrl.includes("?") ? "&" : "?"}v=${Date.now()}`;
    audioRef.current.pause();
    audioRef.current.src = playableUrl;
    audioRef.current.currentTime = 0;
    audioRef.current.load();
    try {
      await audioRef.current.play();
      return true;
    } catch {
      return false;
    }
  }

  const allCardsSelected = useMemo(
    () => !!session && session.cards.length > 0 && session.cards.every((card) => card.selected),
    [session],
  );

  useEffect(() => {
    if (!session?.audio_url || !audioRef.current) {
      return;
    }
    void playAudioUrl(session.audio_url).then((started) => {
      if (!started) {
        setStatus("Voice audio is ready. Tap the play button below if it does not start automatically.");
      }
    });
  }, [session?.audio_url, audioPlaybackToken]);

  useEffect(() => {
    if (!allCardsSelected || !playPlanButtonRef.current) {
      return;
    }
    playPlanButtonRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [allCardsSelected]);

  useEffect(() => {
    if (!session?.session_id || !(session.cards?.length || 0)) {
      return;
    }
    void prewarmAudio(session.session_id).catch(() => {
      // Background optimization only.
    });
  }, [session?.session_id, session?.cards?.length]);

  const canSubmit = useMemo(
    () => form.child_name.trim() && form.event_name.trim() && !loadingCards,
    [form.child_name, form.event_name, loadingCards],
  );

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoadingCards(true);
    setStatus("Creating flash cards...");
    try {
      const nextSession = await createSession(form);
      setSession(nextSession);
      setStatus(nextSession.status);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to create flash cards.");
    } finally {
      setLoadingCards(false);
    }
  }

  async function handleSelectCard(card: FlashCard) {
    if (!session || loadingChoiceIndex !== null) {
      return;
    }
    setLoadingChoiceIndex(card.index);
    setStatus(`Adding ${card.label} to the plan...`);
    try {
      const nextSession = await selectCard(session.session_id, card.index);
      setSession(nextSession);
      setAudioPlaybackToken((current) => current + 1);
      if (nextSession.audio_url) {
        const started = await playAudioUrl(nextSession.audio_url);
        setStatus(
          started
            ? nextSession.status
            : "Voice is ready. Tap the play button below to hear this card.",
        );
      } else {
        setStatus("This card was added, but voice audio could not be created. Check OPENAI_API_KEY on the server.");
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to add that card.");
    } finally {
      setLoadingChoiceIndex(null);
    }
  }

  async function handlePlayCompletePlan() {
    if (!session || playingFullPlan) {
      return;
    }
    setPlayingFullPlan(true);
    setStatus("Preparing the complete selected plan...");
    try {
      const nextSession = await playCompletePlan(session.session_id);
      setSession(nextSession);
      setAudioPlaybackToken((current) => current + 1);
      if (nextSession.audio_url) {
        const started = await playAudioUrl(nextSession.audio_url);
        setStatus(
          started
            ? nextSession.status
            : "Voice audio is ready. Tap the play button below if it does not start automatically.",
        );
      } else {
        setStatus("The complete plan audio was created, but playback could not start.");
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to play the complete plan.");
    } finally {
      setPlayingFullPlan(false);
    }
  }

  function handleClearAll() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    setForm(INITIAL_FORM);
    setSession(null);
    setStatus("Everything was cleared. Enter a new event to create a new set of picture cards.");
    setLoadingCards(false);
    setLoadingChoiceIndex(null);
    setPlayingFullPlan(false);
  }

  return (
    <main className="min-h-screen bg-linear-to-b from-sky-50 to-white text-slate-900">
      <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-6 px-4 py-5 md:px-6 lg:px-8">
        <header className="rounded-[2rem] bg-white/90 p-5 shadow-lg shadow-sky-100 ring-1 ring-sky-100 md:p-6">
          <p className="text-sm font-semibold uppercase tracking-[0.24em] text-sky-700">Living Social Stories</p>
          <h1 className="mt-2 text-3xl font-bold text-slate-900 md:text-4xl">Calm picture plans for real-life events</h1>
          <p className="mt-3 max-w-3xl text-base leading-7 text-slate-600 md:text-lg">
            Add a real event, who is going with the child, and the steps you want in the plan. The child taps large
            picture cards below, and each selected card is added to the plan strip with spoken audio.
          </p>
        </header>

        <section className="grid gap-6 xl:grid-cols-[1.05fr_1.35fr]">
          <form
            onSubmit={handleCreate}
            className="rounded-[2rem] bg-white p-5 shadow-lg shadow-sky-100 ring-1 ring-sky-100 md:p-6"
          >
            <div className="mb-5">
              <h2 className="text-2xl font-bold text-slate-900">1. Parent Setup</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                Enter the real event, the caregiver, and the steps you want the child to see.
              </p>
            </div>

            <div className="grid gap-4">
              <label className="grid gap-2">
                <span className="text-sm font-semibold text-slate-700">Child&apos;s name</span>
                <input
                  className="h-14 rounded-2xl border border-sky-100 bg-sky-50 px-4 text-lg outline-none transition focus:border-sky-400 focus:bg-white"
                  value={form.child_name}
                  onChange={(event) => setForm((current) => ({ ...current, child_name: event.target.value }))}
                  placeholder="Aarav"
                />
              </label>

              <label className="grid gap-2">
                <span className="text-sm font-semibold text-slate-700">Age</span>
                <input
                  className="h-14 rounded-2xl border border-sky-100 bg-sky-50 px-4 text-lg outline-none transition focus:border-sky-400 focus:bg-white"
                  type="number"
                  min={5}
                  max={10}
                  value={form.age}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, age: Number(event.target.value || current.age) }))
                  }
                />
              </label>

              <label className="grid gap-2">
                <span className="text-sm font-semibold text-slate-700">What is the event?</span>
                <textarea
                  className="min-h-28 rounded-2xl border border-sky-100 bg-sky-50 px-4 py-3 text-lg outline-none transition focus:border-sky-400 focus:bg-white"
                  value={form.event_name}
                  onChange={(event) => setForm((current) => ({ ...current, event_name: event.target.value }))}
                  placeholder="Doctor visit for a checkup tomorrow morning"
                />
                <span className="text-xs leading-5 text-slate-500">
                  Describe the actual situation so the cards can match the event.
                </span>
              </label>

              <label className="grid gap-2">
                <span className="text-sm font-semibold text-slate-700">Who will go with the child?</span>
                <input
                  className="h-14 rounded-2xl border border-sky-100 bg-sky-50 px-4 text-lg outline-none transition focus:border-sky-400 focus:bg-white"
                  value={form.caregiver_name}
                  onChange={(event) => setForm((current) => ({ ...current, caregiver_name: event.target.value }))}
                  placeholder="Mom, Dad, Grandma, or Teacher"
                />
                <span className="text-xs leading-5 text-slate-500">
                  This name or role will be used in the story and voice.
                </span>
              </label>

              <label className="grid gap-2">
                <span className="text-sm font-semibold text-slate-700">Which steps do you want in the plan?</span>
                <textarea
                  className="min-h-24 rounded-2xl border border-sky-100 bg-sky-50 px-4 py-3 text-lg outline-none transition focus:border-sky-400 focus:bg-white"
                  value={form.suggested_steps}
                  onChange={(event) => setForm((current) => ({ ...current, suggested_steps: event.target.value }))}
                  placeholder="Check in, sit in the waiting room, listen to the doctor, choose a sticker"
                />
                <span className="text-xs leading-5 text-slate-500">
                  Add real steps in order, separated by commas. These are used first before any fallback planning.
                </span>
              </label>

              <label className="grid gap-2">
                <span className="text-sm font-semibold text-slate-700">How many picture cards do you want?</span>
                <input
                  className="h-14 rounded-2xl border border-sky-100 bg-sky-50 px-4 text-lg outline-none transition focus:border-sky-400 focus:bg-white"
                  type="number"
                  min={1}
                  max={20}
                  value={form.activity_count}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      activity_count: Math.max(1, Math.min(20, Number(event.target.value || current.activity_count))),
                    }))
                  }
                />
                <span className="text-xs leading-5 text-slate-500">Use a smaller number for a shorter plan.</span>
              </label>
            </div>

            <div className="mt-5 flex flex-col gap-3">
              <div className="grid gap-3 md:grid-cols-2">
                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="h-16 rounded-2xl bg-sky-600 px-6 text-lg font-bold text-white shadow-lg shadow-sky-200 transition enabled:hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  {loadingCards ? "Creating Flash Cards..." : "Create Flash Cards"}
                </button>
                <button
                  type="button"
                  onClick={handleClearAll}
                  disabled={loadingCards}
                  className="h-16 rounded-2xl border border-sky-200 bg-white px-6 text-lg font-bold text-sky-800 shadow-sm transition enabled:hover:bg-sky-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
                >
                  Clear All
                </button>
              </div>
              <div className="rounded-2xl bg-sky-50 px-4 py-3 text-sm leading-6 text-sky-900">{status}</div>
            </div>
          </form>

          <section className="rounded-[2rem] bg-white p-5 shadow-lg shadow-sky-100 ring-1 ring-sky-100 md:p-6">
            <div className="mb-5 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
              <div>
                <h2 className="text-2xl font-bold text-slate-900">2. Flash Cards</h2>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  Tap a card to hear it and add it to the child&apos;s plan.
                </p>
              </div>
              {session ? (
                <div className="rounded-full bg-sky-50 px-4 py-2 text-sm font-semibold text-sky-800">
                  {session.child_name}&apos;s plan for {session.event_name}
                </div>
              ) : null}
            </div>

            {loadingCards ? (
              <div className="mb-4 rounded-2xl bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-900 ring-1 ring-amber-200">
                Generating Flashcards...
              </div>
            ) : null}

            <div className="grid gap-4 md:grid-cols-2">
              {(session?.cards || []).map((card) => {
                const isLoading = loadingChoiceIndex === card.index;
                return (
                  <button
                    key={card.id}
                    type="button"
                    onClick={() => handleSelectCard(card)}
                    disabled={isLoading}
                    aria-label={card.label}
                    className={`group overflow-hidden rounded-[2rem] border-4 bg-slate-50 text-left shadow-md transition ${
                      card.selected
                        ? "border-emerald-400 shadow-emerald-100"
                        : "border-transparent hover:border-sky-200 active:scale-[0.99]"
                    } ${isLoading ? "animate-pulse" : ""}`}
                  >
                    {card.image_url ? (
                      <img
                        src={card.image_url}
                        alt={card.label}
                        className="h-72 w-full object-cover md:h-80"
                      />
                    ) : (
                      <div className="flex h-72 items-center justify-center bg-sky-100 text-lg font-semibold text-sky-700 md:h-80">
                        Card image loading
                      </div>
                    )}
                    <div className="flex items-center justify-between gap-3 px-4 py-4">
                      <span className="text-base font-semibold text-slate-800 md:text-lg">{card.label}</span>
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-bold uppercase tracking-[0.18em] ${
                          card.selected ? "bg-emerald-100 text-emerald-800" : "bg-sky-100 text-sky-800"
                        }`}
                      >
                        {card.selected ? "Added" : "Tap"}
                      </span>
                    </div>
                  </button>
                );
              })}

              {!session && !loadingCards ? (
                <div className="md:col-span-2 flex min-h-80 items-center justify-center rounded-[2rem] border-2 border-dashed border-sky-200 bg-sky-50 text-center text-lg leading-8 text-slate-500">
                  The picture flash cards will appear here after the parent creates the story plan.
                </div>
              ) : null}
            </div>

            <div className="mt-6 rounded-[2rem] bg-slate-50 p-4 ring-1 ring-slate-100">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-xl font-bold text-slate-900">My Plan</h3>
                <span className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-slate-600">
                  {(session?.cards || []).filter((card) => card.selected).length} of {session?.cards.length || 0} selected
                </span>
              </div>

              {(session?.selected_cards || []).length ? (
                <>
                  <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
                    {session?.selected_cards.map((card) => (
                      <div key={card.id} className="overflow-hidden rounded-[1.5rem] bg-white shadow-sm ring-1 ring-emerald-100">
                        {card.image_url ? (
                          <img src={card.image_url} alt={card.label} className="h-32 w-full object-cover" />
                        ) : (
                          <div className="flex h-32 items-center justify-center bg-sky-100 text-sm text-sky-700">Selected</div>
                        )}
                        <div className="px-3 py-3 text-sm font-semibold text-slate-700">{card.label}</div>
                      </div>
                    ))}
                  </div>
                  {!allCardsSelected && (session?.cards.length || 0) > 0 ? (
                    <p className="mt-4 rounded-2xl bg-white px-4 py-3 text-sm leading-6 text-slate-600">
                      Tap every picture card above to unlock Play Complete Plan.
                    </p>
                  ) : null}
                  {allCardsSelected ? (
                    <button
                      ref={playPlanButtonRef}
                      type="button"
                      onClick={handlePlayCompletePlan}
                      disabled={playingFullPlan || loadingCards}
                      className="mt-4 h-14 w-full rounded-2xl bg-emerald-600 px-5 text-base font-bold text-white shadow-md transition enabled:hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-slate-300"
                    >
                      {playingFullPlan ? "Preparing Full Plan..." : "Play Complete Plan"}
                    </button>
                  ) : null}
                </>
              ) : (
                <div className="rounded-[1.5rem] border-2 border-dashed border-slate-200 bg-white px-4 py-10 text-center text-base text-slate-500">
                  The selected flash cards will collect here in order.
                </div>
              )}
            </div>

            <div className="mt-6 rounded-[2rem] bg-sky-50 p-4 ring-1 ring-sky-100">
              <div className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-sky-700">Voice</div>
              <audio
                ref={audioRef}
                controls
                playsInline
                preload="auto"
                className="w-full"
                key={session?.audio_url || "empty"}
              >
                {session?.audio_url ? (
                  <source src={session.audio_url} type={audioMimeType(session.audio_url)} />
                ) : null}
              </audio>
            </div>
          </section>
        </section>
      </div>
    </main>
  );
}

export default App;
