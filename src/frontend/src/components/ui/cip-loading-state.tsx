export function CipLoadingState({
  label = "Loading institutional intelligence...",
}: {
  label?: string;
}) {
  return (
    <div className="rounded-[24px] border border-[#E5DFDC] bg-white p-8">
      <div className="flex items-center gap-3">
        <div className="size-2 animate-pulse rounded-full bg-[#7A2438]" />
        <span className="text-[13px] text-[#6F686B]">
          {label}
        </span>
      </div>
    </div>
  );
}
