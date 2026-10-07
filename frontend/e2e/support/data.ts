/**
 * Input texts for the end-to-end tests. Everything here is invented: the
 * people, employers, schools and job postings do not exist.
 *
 * The layout (section headings, "Title - Employer (dates)" lines, "- "
 * bullets) is what the deterministic fake provider reads.
 */
import type { JobText } from './steps.ts'

/** A second, clearly different person for the isolation tests. */
export const SECOND_PERSON = {
  name: 'Morgan Lee',
  role: 'Data Analyst at Pinecrest Outfitters',
  resume: `Morgan Lee
Denver, CO | morgan.lee@example.com

Experience
Data Analyst - Pinecrest Outfitters (Mar 2021 - Present)
- Built weekly sales dashboards in Tableau for 12 store managers
- Automated a monthly inventory report with Python and pandas

Education
B.A. in Economics - Westbrook College (Sep 2016 - May 2020)

Skills
Tools: Tableau, Python, pandas, Excel
`,
}

/** Markup that would run script or load an image if it were ever inserted as HTML. */
export const SCRIPT_TAG = '<script>window.__xss=1</script>'
export const IMG_TAG = '<img src=x onerror="window.__xss=1">'

export const HOSTILE = {
  role: 'Web Developer at Lumen Works',
  scriptBullet: `Built a comment form in JavaScript that escapes ${SCRIPT_TAG} tags in user posts`,
  imgBullet: `Fixed an image gallery bug ${IMG_TAG} reported by 12 customers`,
  boldBullet: 'Wrote React components for the <b>bold</b> account settings page',
  get resume(): string {
    return `Sam Carter
Portland, OR | sam.carter@example.com

Experience
Web Developer - Lumen Works (Jan 2022 - Present)
- ${this.scriptBullet}
- ${this.imgBullet}
- ${this.boldBullet}

Skills
Languages: JavaScript, HTML, CSS, React
`
  },
  job: {
    title: `Front-End Developer ${IMG_TAG}`,
    company: `Maplewick ${SCRIPT_TAG}`,
    description: `About the role
Maplewick builds web tools for small shops.

Requirements
- Experience building forms in JavaScript ${IMG_TAG}
- Experience writing React components
- Experience with HTML and CSS ${SCRIPT_TAG}
`,
  } satisfies JobText,
}

const EMPLOYERS = [
  'Alderwick Systems',
  'Brambleton Data',
  'Copperfield Labs',
  'Dunmere Logistics',
  'Eastmoor Health',
  'Fennimore Robotics',
  'Glenhaven Media',
  'Hartwell Energy',
  'Ivybridge Finance',
  'Juniper Hollow Games',
  'Kestrel Point Travel',
  'Larkspur Retail',
  'Merrowdown Insurance',
  'Northgate Telecom',
  'Oakhurst Education',
  'Pembury Analytics',
]

const TECHNOLOGIES = [
  'Python',
  'TypeScript',
  'PostgreSQL',
  'Docker',
  'React',
  'FastAPI',
  'Redis',
  'Terraform',
  'Kafka',
  'GraphQL',
  'Go',
  'Java',
  'Rust',
  'Elasticsearch',
  'Airflow',
  'Spark',
]

const BULLET_ENDINGS = [
  'coordinating with 6 engineers across 3 teams to migrate legacy batch jobs, document operational runbooks and review pull requests every week',
  'reducing recurring incident volume through better alerting, capacity planning and automated regression testing before each monthly release',
  'writing design documents, running structured onboarding sessions for new colleagues and presenting quarterly results to the wider department',
]

/** A profile long enough that the tailored resume cannot fit on one printed page. */
export function longResume(): string {
  const roles = EMPLOYERS.map((employer, index) => {
    const technology = TECHNOLOGIES[index]
    const startYear = 2024 - index
    const bullets = BULLET_ENDINGS.map(
      (ending, position) =>
        `- Maintained the ${employer} ${technology} service ${position + 1} of the platform group, ${ending}`,
    )
    return [`Platform Engineer ${index + 1} - ${employer} (Jan ${startYear} - Dec ${startYear})`, ...bullets].join('\n')
  })
  return `Casey Whitlock
Madison, WI | casey.whitlock@example.com

Experience
${roles.join('\n\n')}

Education
B.S. in Software Engineering - Thornbury University (Sep 2004 - Jun 2008)

Skills
Languages: ${TECHNOLOGIES.join(', ')}
`
}

/** A job posting with one requirement per technology of the long profile. */
export function longJob(): JobText {
  const requirements = TECHNOLOGIES.map(
    (technology) => `- Experience maintaining production services with ${technology}`,
  )
  return {
    title: 'Staff Platform Engineer',
    company: 'Quarrybank Cloud',
    description: `About the role
Quarrybank Cloud runs shared infrastructure for product teams and is hiring a Staff Platform Engineer.

Requirements
${requirements.join('\n')}
`,
  }
}
