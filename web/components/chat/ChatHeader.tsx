export type ChatMode = 'base' | 'apm';

interface ChatHeaderProps {
  onOpenSettings: () => void;
  onClearHistory: () => void;
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
}

export function ChatHeader({ onOpenSettings, onClearHistory, mode, onModeChange }: ChatHeaderProps) {
  return (
    <header className="mb-4 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">OpenPoke 🌴</h1>
        <select
          value={mode}
          onChange={(e) => onModeChange(e.target.value as ChatMode)}
          className="rounded-md border border-gray-200 px-2 py-1.5 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-blue-500"
          title="Select chat endpoint"
        >
          <option value="base">Base (OpenRouter)</option>
          <option value="apm">APM (Local LangGraph)</option>
        </select>
        {mode === 'apm' && (
          <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
            APM active
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          className="rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50"
          onClick={onOpenSettings}
        >
          Settings
        </button>
        <button
          className="rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50"
          onClick={onClearHistory}
        >
          Clear
        </button>
      </div>
    </header>
  );
}
