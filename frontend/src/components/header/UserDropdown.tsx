import { useState } from "react";
import { useNavigate } from "react-router";
import { Dropdown } from "../ui/dropdown/Dropdown";
import { useAuth } from "../../context/AuthContext";

export default function UserDropdown() {
  const [isOpen, setIsOpen] = useState(false);
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  const displayName = user?.name || "CliniqAI User";
  const role = user?.role || "staff";
  const initials = displayName
    .split(" ")
    .map((part) => part[0]?.toUpperCase())
    .filter(Boolean)
    .slice(0, 2)
    .join("");

  function toggleDropdown() {
    setIsOpen((prev) => !prev);
  }

  function closeDropdown() {
    setIsOpen(false);
  }

  function onSignOut() {
    signOut();
    closeDropdown();
    navigate("/signin");
  }

  return (
    <div className="relative">
      <button onClick={toggleDropdown} className="flex items-center text-gray-700 dropdown-toggle dark:text-gray-300">
        <span className="mr-3 grid h-11 w-11 place-items-center rounded-full bg-brand-500 text-sm font-semibold text-white">
          {initials || "CA"}
        </span>
        <span className="block mr-1 font-medium text-theme-sm">{displayName}</span>
        <svg
          className={`stroke-gray-500 dark:stroke-gray-300 transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
          width="18"
          height="20"
          viewBox="0 0 18 20"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M4.3125 8.65625L9 13.3437L13.6875 8.65625" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      <Dropdown
        isOpen={isOpen}
        onClose={closeDropdown}
        className="absolute right-0 mt-[17px] flex w-[260px] flex-col rounded-2xl border border-gray-200 bg-white p-3 shadow-theme-lg dark:border-gray-800 dark:bg-black"
      >
        <div>
          <span className="block font-medium text-gray-700 text-theme-sm dark:text-white">{displayName}</span>
          <span className="mt-0.5 block text-theme-xs text-gray-500 dark:text-gray-300">{user?.email || "staff@cliniqai.demo"}</span>
          <span className="mt-1 inline-flex rounded-full bg-brand-100 px-2.5 py-1 text-xs font-semibold text-brand-700 dark:bg-brand-900/30 dark:text-brand-300">
            {role}
          </span>
        </div>

        <div className="my-3 h-px bg-gray-200 dark:bg-gray-800" />

        <button
          type="button"
          onClick={onSignOut}
          className="flex items-center gap-3 px-3 py-2 font-medium text-gray-700 rounded-lg text-theme-sm hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-900"
        >
          Sign out
        </button>
      </Dropdown>
    </div>
  );
}

