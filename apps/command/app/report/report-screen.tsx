"use client";

import Link from "next/link";
import type { Route } from "next";
import { ArrowLeft } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { LanguageToggle } from "@/components/varuna/language-toggle";
import { PageHeader } from "@/components/varuna/page-header";
import { ReportWizard } from "@/components/varuna/report-wizard";
import { Wordmark } from "@/components/varuna/wordmark";
import { usePublicT } from "@/lib/i18n";

const MAP_ROUTE = "/map" as Route;

/** Mobile-first citizen report flow (SPEC.md section 7.11), in English, Hindi or Marathi. */
export function ReportScreen() {
  const t = usePublicT("report");
  return (
    <main className="mx-auto flex w-full max-w-[560px] flex-col gap-6 px-4 py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Wordmark size="sm" withMark />
        <LanguageToggle />
      </div>
      {/* Navigation, so a real link rather than a link announced as a button, as the
          confirmation's links are. */}
      <Link
        href={MAP_ROUTE}
        className={buttonVariants({ variant: "ghost", size: "lg", className: "h-11 self-start" })}
      >
        <ArrowLeft aria-hidden="true" />
        {t("backToMap")}
      </Link>

      <PageHeader title={t("title")} description={t("description")} honesty={t("honesty")} />

      <ReportWizard />
    </main>
  );
}
