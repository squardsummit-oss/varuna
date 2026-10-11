/**
 * How the public site describes itself to search engines and AI assistants.
 *
 * The page titles used to be the slogan alone ("VARUNA - every street, three hours early"), which
 * says nothing a person searching for "urban flood nowcasting" would type. These strings name the
 * subject in plain words. They describe the prototype as it is: no ranking claims, and the replay
 * is called a reconstruction, as it is everywhere else on the site (rule 6).
 */
import { siteUrl } from "./site";

export const SITE_NAME = "VARUNA";

/** Under 60 characters, so search results show it whole. */
export const SITE_TITLE = "VARUNA - Urban flood nowcasting, street by street";

/** Under 160 characters, so search results show it whole. */
export const SITE_DESCRIPTION =
  "Urban flood nowcasting prototype: radar rainfall through coupled surface and drain models to street-level flood depth 0-3 h ahead, with emergency routes.";

/** The longer account the structured data carries. */
export const LONG_DESCRIPTION =
  "VARUNA is an open-source urban flood nowcasting prototype built for Smart India Hackathon 2026 (problem SIH26085, Ministry of Earth Sciences). It turns radar rainfall into street-by-street flood-depth forecasts for the next three hours by coupling a 2D local-inertial surface model on a 30 m terrain with a 1D drainage network, estimates drain blockage from observations with an ensemble Kalman filter, and routes emergency vehicles around streets forecast to be impassable. It runs on a reconstructed replay of the 2 July 2019 Mumbai flood and on live radar and weather-model rainfall.";

export const KEYWORDS = [
  "urban flood nowcasting",
  "flood forecasting",
  "street-level flood depth",
  "urban drainage model",
  "rainfall-runoff",
  "coupled 1D-2D hydraulic model",
  "flood digital twin",
  "radar nowcasting",
  "pySTEPS",
  "Mumbai floods",
  "flood-aware routing",
  "Smart India Hackathon 2026",
  "SIH26085",
];

export const SOURCE_REPOSITORY = "https://github.com/squardsummit-oss/varuna";
export const DEMO_VIDEO = "https://www.youtube.com/watch?v=tyVrHbtSRnM";

/**
 * Google Search Console's ownership checks for varuna-dhrishta.vercel.app. The first is the
 * HTML-tag code Search Console issues for the URL-prefix property, which is the one that verifies:
 * a vercel.app subdomain cannot carry a DNS TXT record. The second is the team's DNS-record code,
 * kept because emitting it costs nothing. Public by design; they prove who may manage the site's
 * search listing, nothing more.
 */
export const GOOGLE_SITE_VERIFICATION = [
  "uYLMFT615ziODu913VvRSyeIpBJwXTFoxMmXvdcOh8k",
  "3-tJ5Ovse3gy397Za7ymLauLLhIDPusVbmzkyMlQPzo",
];
export const TEAM_NAME = "Team VARUNA, Vellore Institute of Technology";

/** schema.org JSON-LD for the landing page: the site, the application and its source code. */
export function structuredData(base: string = siteUrl()): Record<string, unknown> {
  const subjects = [
    "Urban flooding",
    "Flood forecasting",
    "Nowcasting",
    "Urban drainage",
    "Hydrology",
    "Digital twin",
  ];
  const creator = { "@type": "Organization", name: TEAM_NAME, url: SOURCE_REPOSITORY };
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${base}/#website`,
        name: SITE_NAME,
        url: base,
        description: SITE_DESCRIPTION,
        inLanguage: "en",
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${base}/#app`,
        name: SITE_NAME,
        url: base,
        description: LONG_DESCRIPTION,
        applicationCategory: "Urban flood nowcasting",
        operatingSystem: "Web browser",
        isAccessibleForFree: true,
        keywords: KEYWORDS.join(", "),
        about: subjects.map((name) => ({ "@type": "Thing", name })),
        spatialCoverage: [
          { "@type": "Place", name: "Mumbai, India" },
          { "@type": "Place", name: "Chennai, India" },
        ],
        creator,
        sameAs: [SOURCE_REPOSITORY, DEMO_VIDEO],
      },
      {
        "@type": "SoftwareSourceCode",
        "@id": `${base}/#code`,
        name: SITE_NAME,
        description: LONG_DESCRIPTION,
        codeRepository: SOURCE_REPOSITORY,
        programmingLanguage: ["Python", "TypeScript", "Rust"],
        license: "https://opensource.org/licenses/MIT",
        creator,
        targetProduct: { "@id": `${base}/#app` },
      },
    ],
  };
}
