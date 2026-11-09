import type { Metadata } from "next";

import { WaitlistPageClient } from "./waitlist-page-client";

export const metadata: Metadata = {
  title: "Join the beta | Financial Bot",
  description: "Request access to the Financial Bot cloud beta.",
};

export default function WaitlistPage() {
  return <WaitlistPageClient />;
}
