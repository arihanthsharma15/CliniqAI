import { createContext, useContext, useMemo, useState } from "react";

export type SearchField = "all" | "patient" | "task_id" | "callback" | "request" | "status" | "call_ref";

type SearchContextValue = {
  query: string;
  setQuery: (value: string) => void;
  field: SearchField;
  setField: (value: SearchField) => void;
};

const SearchContext = createContext<SearchContextValue | null>(null);

export function SearchProvider({ children }: { children: React.ReactNode }) {
  const [query, setQuery] = useState("");
  const [field, setField] = useState<SearchField>("all");
  const value = useMemo(() => ({ query, setQuery, field, setField }), [query, field]);
  return <SearchContext.Provider value={value}>{children}</SearchContext.Provider>;
}

export function useSearch() {
  const ctx = useContext(SearchContext);
  if (!ctx) throw new Error("useSearch must be used inside SearchProvider");
  return ctx;
}
