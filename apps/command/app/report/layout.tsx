import { PublicI18nProvider } from "@/lib/i18n/provider";

/** The report flow speaks the public map's language: the choice made on `/map` carries over. */
export default function ReportLayout({ children }: { children: React.ReactNode }) {
  return <PublicI18nProvider>{children}</PublicI18nProvider>;
}
