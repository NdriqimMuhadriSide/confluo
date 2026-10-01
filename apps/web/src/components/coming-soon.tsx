export function ComingSoon({ title, card }: { title: string; card: string }) {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground">Arrives with the “{card}” card.</p>
    </>
  );
}
