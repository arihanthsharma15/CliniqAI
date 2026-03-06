import { BrowserRouter as Router, Navigate, Routes, Route } from "react-router";
import type { ReactElement } from "react";
import SignIn from "./pages/AuthPages/SignIn";
import SignUp from "./pages/AuthPages/SignUp";
import NotFound from "./pages/OtherPage/NotFound";
import AppLayout from "./layout/AppLayout";
import { ScrollToTop } from "./components/common/ScrollToTop";
import OperationsDashboard from "./pages/Dashboard/OperationsDashboard";
import { useAuth } from "./context/AuthContext";
import type { DemoRole } from "./context/AuthContext";

function ProtectedLayout() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/signin" replace />;
  return <AppLayout />;
}

function IndexRoute() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/signin" replace />;
  if (user.role === "staff") return <Navigate to="/dashboard/staff" replace />;
  if (user.role === "doctor") return <Navigate to="/dashboard/doctor" replace />;
  return <Navigate to="/signin" replace />;
}

function RoleRoute({ allow, element }: { allow: DemoRole[]; element: ReactElement }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/signin" replace />;
  if (!allow.includes(user.role)) {
    if (user.role === "staff") return <Navigate to="/dashboard/staff" replace />;
    if (user.role === "doctor") return <Navigate to="/dashboard/doctor" replace />;
    return <Navigate to="/signin" replace />;
  }
  return element;
}

export default function App() {
  const { user } = useAuth();
  return (
    <Router>
      <ScrollToTop />
      <Routes>
        <Route element={<ProtectedLayout />}>
          <Route index path="/" element={<IndexRoute />} />
          <Route
            path="/dashboard/staff"
            element={<RoleRoute allow={["staff"]} element={<OperationsDashboard role="staff" />} />}
          />
          <Route
            path="/dashboard/doctor"
            element={<RoleRoute allow={["doctor"]} element={<OperationsDashboard role="doctor" />} />}
          />
        </Route>

        <Route path="/signin" element={user ? <Navigate to="/" replace /> : <SignIn />} />
        <Route path="/signup" element={<SignUp />} />

        <Route path="*" element={<NotFound />} />
      </Routes>
    </Router>
  );
}
