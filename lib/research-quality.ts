/** Warn when a streamed answer bypassed structured evidence validation. */
export function researchOutputNotice(
  response: { payload?: { _meta?: { output_fallback?: string; structured_output_validated?: boolean } } },
  locale: "zh" | "en",
): string | null {
  const meta = response.payload?._meta;
  if (meta?.output_fallback !== "text" || meta.structured_output_validated !== false) return null;
  return locale === "en"
    ? "This answer was not validated. Treat its claims as unverified and check the sources."
    : "这份回答未通过结构化与来源校验，内容尚未核实，请对照来源检查。";
}
