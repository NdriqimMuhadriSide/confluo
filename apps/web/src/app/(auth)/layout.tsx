export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 p-6">
      <p className="text-center text-lg font-semibold tracking-tight">Confluo</p>
      {children}
    </main>
  );
}
