import { authFetch } from "../../api/auth";

export const searchPlaceInCountry = async (query) => {
    const code = localStorage.getItem("code");
    const url = `/api/search-place?country=${code}&query=${encodeURIComponent(query)}`;

    try {
        const response = await authFetch(url);
        console.log('response:', response)
        const data = await response.json();
        if (data.status === "200") {
            const results = extractResults(data);
            const resultsLength = data.results.length
            let i = 0;
            while (i < resultsLength){
                console.log(`${i}: Latitude: ${results[i].lat}, Longitude: ${results[i].lng}, Name: ${results[i].name}`)
                i++;
            }
            console.log('results:',results)
            return results;
        } else {
            console.log('No results found or an error occurred:', data.status);
        }
    } catch (error) {
        console.error('Error fetching data:', error);
    }
};

export const extractResults = (data) => {
    if (data.results && data.results[0]) {
        return data.results.map((item) => {
            return {
                lat: item.geometry.location.lat,
                lng: item.geometry.location.lng,
                name: item.name,

            };
        });
    }
    return { lat: null, lng: null };
};

