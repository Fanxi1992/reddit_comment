type Tab = 'manual' | 'excel'

type PostInputTabsProps = {
  activeTab: Tab
  onChange: (tab: Tab) => void
}

export function PostInputTabs({ activeTab, onChange }: PostInputTabsProps) {
  return (
    <div className="grid grid-cols-2 rounded-md bg-slate-100 p-0.5">
      {[
        ['manual', '手动录入'],
        ['excel', 'Excel 导入'],
      ].map(([value, label]) => (
        <button
          className={`h-7 rounded-md text-[11px] font-semibold transition ${
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
