import { useEffect, useMemo, useState } from "react";
import { Dropdown } from "../ui/dropdown/Dropdown";
import { getNotifications, markAllNotificationsRead, markNotificationRead } from "../../services/opsApi";
import { useAuth } from "../../context/AuthContext";
import type { AppNotification } from "../../types/ops";

type NotificationItem = {
  id: number;
  title: string;
  detail: string;
  tone: "critical" | "normal";
  isRead: boolean;
};

export default function NotificationDropdown() {
  const { user } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<AppNotification[]>([]);

  useEffect(() => {
    if (!user) return;
    const currentRole = user.role;
    let active = true;
    async function load() {
      try {
        const data = await getNotifications(currentRole, true);
        if (!active) return;
        setNotifications(data.slice(0, 20));
      } catch {
        if (!active) return;
        setNotifications([]);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [user]);

  const items = useMemo<NotificationItem[]>(
    () =>
      notifications.slice(0, 10).map((n) => ({
        id: n.id,
        title: n.title,
        detail: n.message,
        tone: n.is_urgent ? "critical" : "normal",
        isRead: n.is_read,
      })),
    [notifications],
  );

  const unreadCount = notifications.filter((n) => !n.is_read).length;
  const notifying = unreadCount > 0 && !isOpen;

  async function toggleDropdown() {
    const next = !isOpen;
    setIsOpen(next);
    if (next && user) {
      try {
        const fresh = await getNotifications(user.role, true);
        setNotifications(fresh.slice(0, 20));
      } catch {
        // keep existing list
      }
    }
    if (next && user && unreadCount > 0) {
      try {
        await markAllNotificationsRead(user.role);
      } catch {
        return;
      }
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
    }
  }

  function closeDropdown() {
    setIsOpen(false);
  }

  async function dismissNotification(id: number) {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
    try {
      await markNotificationRead(id);
    } catch {
      // keep optimistic UI dismiss even if backend call fails
    }
  }

  async function clearAllNotifications() {
    setNotifications([]);
    if (!user) return;
    try {
      await markAllNotificationsRead(user.role);
    } catch {
      // keep optimistic clear
    }
  }

  return (
    <div className="relative">
      <button
        className="relative flex items-center justify-center text-gray-500 transition-colors bg-white border border-gray-200 rounded-full dropdown-toggle hover:text-gray-700 h-11 w-11 hover:bg-gray-100 dark:border-gray-800 dark:bg-black dark:text-gray-300 dark:hover:bg-gray-900 dark:hover:text-white"
        onClick={toggleDropdown}
      >
        <span className={`absolute right-0 top-0.5 z-10 h-2 w-2 rounded-full bg-orange-400 ${!notifying ? "hidden" : "flex"}`}>
          <span className="absolute inline-flex w-full h-full bg-orange-400 rounded-full opacity-75 animate-ping" />
        </span>
        <svg className="fill-current" width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
          <path
            fillRule="evenodd"
            clipRule="evenodd"
            d="M10.75 2.29248C10.75 1.87827 10.4143 1.54248 10 1.54248C9.58583 1.54248 9.25004 1.87827 9.25004 2.29248V2.83613C6.08266 3.20733 3.62504 5.9004 3.62504 9.16748V14.4591H3.33337C2.91916 14.4591 2.58337 14.7949 2.58337 15.2091C2.58337 15.6234 2.91916 15.9591 3.33337 15.9591H4.37504H15.625H16.6667C17.0809 15.9591 17.4167 15.6234 17.4167 15.2091C17.4167 14.7949 17.0809 14.4591 16.6667 14.4591H16.375V9.16748C16.375 5.9004 13.9174 3.20733 10.75 2.83613V2.29248ZM14.875 14.4591V9.16748C14.875 6.47509 12.6924 4.29248 10 4.29248C7.30765 4.29248 5.12504 6.47509 5.12504 9.16748V14.4591H14.875ZM8.00004 17.7085C8.00004 18.1228 8.33583 18.4585 8.75004 18.4585H11.25C11.6643 18.4585 12 18.1228 12 17.7085C12 17.2943 11.6643 16.9585 11.25 16.9585H8.75004C8.33583 16.9585 8.00004 17.2943 8.00004 17.7085Z"
            fill="currentColor"
          />
        </svg>
      </button>
      <Dropdown
        isOpen={isOpen}
        onClose={closeDropdown}
        className="absolute -right-[240px] mt-[17px] flex max-h-[420px] w-[350px] flex-col rounded-2xl border border-gray-200 bg-white p-3 shadow-theme-lg dark:border-gray-800 dark:bg-black sm:w-[361px] lg:right-0"
      >
        <div className="flex items-center justify-between pb-3 mb-3 border-b border-gray-100 dark:border-gray-800">
          <h5 className="text-lg font-semibold text-gray-800 dark:text-white">Notifications</h5>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void clearAllNotifications()}
              className="text-xs font-medium text-error-600 transition hover:text-error-700 dark:text-error-400 dark:hover:text-error-300"
            >
              Clear All
            </button>
            <button onClick={closeDropdown} className="text-gray-500 transition dark:text-gray-300 hover:text-gray-700 dark:hover:text-white">
              Close
            </button>
          </div>
        </div>
        <ul className="space-y-2 overflow-y-auto custom-scrollbar">
          {items.length === 0 ? (
            <li className="rounded-lg border border-gray-100 p-3 text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400">
              No new notifications.
            </li>
          ) : (
            items.map((item) => (
              <li
                key={String(item.id)}
                className={`rounded-lg border p-3 ${
                  item.tone === "critical"
                    ? "border-error-200 bg-error-50 dark:border-error-900 dark:bg-error-950/30"
                    : item.isRead
                    ? "border-gray-100 dark:border-gray-800"
                    : "border-gray-100 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-gray-800 dark:text-white">{item.title}</p>
                    <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">{item.detail}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void dismissNotification(item.id)}
                    className="rounded-md border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-900"
                    title="Dismiss notification"
                    aria-label="Dismiss notification"
                  >
                    X
                  </button>
                </div>
              </li>
            ))
          )}
        </ul>
      </Dropdown>
    </div>
  );
}
