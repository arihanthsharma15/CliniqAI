import PageMeta from "../../components/common/PageMeta";
import AuthLayout from "./AuthPageLayout";
import SignUpForm from "../../components/auth/SignUpForm";

export default function SignUp() {
  return (
    <>
      <PageMeta
        title="CliniqAI Registration"
        description="Register access for CliniqAI healthcare operations dashboard"
      />
      <AuthLayout>
        <SignUpForm />
      </AuthLayout>
    </>
  );
}
