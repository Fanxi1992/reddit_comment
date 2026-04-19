type Tab = 'manual' | 'excel'

type PostInputTabsProps = {
  activeTab: Tab
  onChange: (tab: Tab) => void
}

export function PostInputTabs({ activeTab, onChange }: PostInputTabsProps) {
  return (
    <div className="grid grid-cols-2 rounded-md bg-slate-100 p-1">
      {[
        ['manual', '手动录入'],
        ['excel', 'Excel 导入'],
      ].map(([value, label]) => (
        <button
          className={`h-8 rounded-md text-xs font-semibold transition ${
            activeTab === value ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-900'
          }`}
          key={value}
          onClick={() => onChange(value as Tab)}
          type="button"
        >
          {label}
        </button>
      ))}
    </div>
  )
}
