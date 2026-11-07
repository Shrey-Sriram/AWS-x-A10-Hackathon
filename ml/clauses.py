"""Train a small clause router using authored examples; preserve text for review."""
from pathlib import Path
import joblib
from sklearn.pipeline import make_pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

EXAMPLES = {
    "Parcel size and boundaries": ["The parcel comprises 85 acres", "The property extends north to the river", "Boundaries follow the eastern fence", "The leased premises cover 200 hectares", "Survey coordinates define the site perimeter"],
    "Operational restriction": ["Suspend work above 35 C", "No excavation within 50 meters of the stream", "Construction must stop during high winds", "No work when temperature exceeds 30 degrees Celsius", "Heavy vehicles may only use access roads"],
    "Term or deadline": ["The lease expires on 2029-06-30", "Renewal notice is due by July 1", "The term begins on January 1", "Operations are permitted between May and October", "Notify the owner within thirty days"],
    "Environmental obligation": ["Maintain vegetation buffers", "Prevent erosion and sediment discharge", "Monitor soil moisture near the wetland", "Restore native plants after grading", "Protect air quality during construction"],
    "Other": ["Rent shall be paid monthly", "The parties agree to arbitration", "The owner is entitled to compensation", "Payment is by bank transfer", "Definitions apply to this agreement"]
}

if __name__ == "__main__":
    pairs = [(s,label) for label,examples in EXAMPLES.items() for s in examples]
    model = make_pipeline(TfidfVectorizer(ngram_range=(1,2)), LogisticRegression(C=5,random_state=73))
    model.fit([p[0] for p in pairs],[p[1] for p in pairs])
    joblib.dump(model,Path(__file__).with_name("clauses.joblib"))
