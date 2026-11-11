import type { Metadata } from "next";

import { AdminWaitlistClient } from "./admin-waitlist-client";

export const metadata: Metadata = {
  title: "Waitlist administration | Financial Bot",
  robots: { index: false, follow: false },
};

export default function AdminWaitlistPage() {
  return <AdminWaitlistClient />;
}
